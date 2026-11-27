"""
Online payment, the foundation (ADR 0050): the three tables, the Stripe
settings, the provider interface with its Stripe and in-memory versions,
the settings helper, and the two admin pages.

No test here holds a real key or reaches the network: every HTTP call is
a mock, and `PaymentTestCase` makes a real one fail the test.
"""

import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import requests
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import signing
from django.db import IntegrityError, OperationalError, ProgrammingError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from trips.tests import make_trip_request

from . import payments, stripe_keys
from .models import (
    DEFAULT_REFUND_POLICY,
    DEFAULT_WHAT_PAYING_MEANS,
    Payment,
    PaymentEvent,
    PaymentStatus,
    PaymentTerms,
    Quote,
    RateCard,
)
from .payment_testing import BLANK_STRIPE_SETTINGS, PaymentTestCase

# Made-up values in the shape of Stripe's, too short to be real keys.
TEST_KEY = "sk_test_notakey"
LIVE_KEY = "rk_live_notakey"
HOOK = "whsec_notasecret"
QUOTE_ID = "0b0e7b1c-58a4-4e0c-9d52-3f8a6c1d2e4f"
SUCCESS = "https://admin.example.test/pay/return/?session_id={CHECKOUT_SESSION_ID}"
CANCEL = "https://www.example.test/payment/cancelled"


def make_quote(**overrides) -> Quote:
    card = RateCard.objects.create(name="TEST card", effective_from=date(2027, 1, 1),
                                   base_ambulatory=Decimal("30.00"))
    fields = {"trip_request": make_trip_request(), "rate_card": card, "amount": Decimal("64.80"),
              "sent_to": "payer@example.com", "lines": []}
    fields.update(overrides)
    return Quote.objects.create(**fields)


def create_args(**overrides) -> dict:
    args = {"amount_cents": 6480, "reference": "#3FA9C2D1", "quote_id": QUOTE_ID,
            "idempotency_key": f"{QUOTE_ID}-1", "success_url": SUCCESS, "cancel_url": CANCEL,
            "expires_at": timezone.now().replace(microsecond=0) + timedelta(minutes=60)}
    args.update(overrides)
    return args


class Reply:
    """What `requests` hands back, as far as the provider reads it."""

    def __init__(self, status=200, body=None, request_id="req_TEST123", text=None):
        self.status_code = status
        self._body = body
        self._text = text
        self.headers = {"Request-Id": request_id} if request_id else {}

    def json(self):
        if self._text is not None:
            raise ValueError("not JSON")
        return self._body


def session_body(**overrides) -> dict:
    body = {
        "id": "cs_test_a1B2c3", "object": "checkout.session", "status": "open", "payment_status": "unpaid",
        "mode": "payment", "amount_total": 6480, "currency": "usd", "payment_intent": None, "livemode": False,
        "metadata": {"quote": QUOTE_ID}, "client_reference_id": QUOTE_ID, "expires_at": 1900000000,
        "url": "https://checkout.stripe.com/c/pay/cs_test_a1B2c3#fid",
    }
    body.update(overrides)
    return body


# --- Keys and settings --------------------------------------------------------


class StripeKeyRuleTests(SimpleTestCase):
    def test_a_key_is_live_test_or_not_a_key(self):
        for key, mode in (("sk_live_x", "live"), ("rk_live_x", "live"), ("sk_test_x", "test"), ("rk_test_x", "test"),
                          ("pk_live_x", ""), ("pk_test_x", ""), ("whsec_x", ""), ("", ""), (None, "")):
            with self.subTest(key=key):
                self.assertEqual(stripe_keys.key_mode(key), mode)

    def test_payments_are_configured_only_with_both_values_and_a_key_that_may_be_used(self):
        configured = stripe_keys.configured
        self.assertTrue(configured(LIVE_KEY, HOOK, debug=False, allow_test=False))
        # Half-configured is not configured.
        self.assertFalse(configured(LIVE_KEY, "", debug=False, allow_test=False))
        self.assertFalse(configured("", HOOK, debug=False, allow_test=False))
        self.assertFalse(configured("", "", debug=True, allow_test=True))
        self.assertFalse(configured(LIVE_KEY, " , ", debug=False, allow_test=False))
        # A test key is refused on a real deployment unless testing was switched on.
        self.assertFalse(configured(TEST_KEY, HOOK, debug=False, allow_test=False))
        self.assertTrue(configured(TEST_KEY, HOOK, debug=False, allow_test=True))
        self.assertTrue(configured(TEST_KEY, HOOK, debug=True, allow_test=False))
        # The publishable key pasted in the secret key's place is no key at all.
        self.assertFalse(configured("pk_live_notakey", HOOK, debug=True, allow_test=True))

    def test_the_webhook_setting_may_hold_two_secrets(self):
        self.assertEqual(stripe_keys.webhook_secrets("whsec_old, whsec_new"), ["whsec_old", "whsec_new"])
        self.assertEqual(stripe_keys.webhook_secrets("whsec_one"), ["whsec_one"])
        self.assertEqual(stripe_keys.webhook_secrets(""), [])
        with override_settings(STRIPE_WEBHOOK_SECRET="whsec_old,whsec_new"):
            self.assertEqual(payments.webhook_secrets(), ["whsec_old", "whsec_new"])

    def test_the_test_run_holds_no_key_whatever_is_in_the_local_env_file(self):
        """`manage.py test` drops the Stripe values before the settings read them."""
        for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_ALLOW_TEST"):
            self.assertNotIn(name, os.environ)
        self.assertEqual(settings.STRIPE_SECRET_KEY, "")
        self.assertEqual(settings.STRIPE_WEBHOOK_SECRET, "")
        self.assertIs(settings.STRIPE_ALLOW_TEST, False)
        self.assertIs(settings.PAYMENTS_CONFIGURED, False)

    def test_the_env_example_names_the_three_variables_with_empty_values(self):
        lines = (Path(settings.BASE_DIR) / ".env.example").read_text(encoding="utf-8").splitlines()
        for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_ALLOW_TEST"):
            self.assertIn(f"{name}=", lines)


class ProviderChoiceTests(PaymentTestCase):
    def test_under_test_settings_the_provider_is_the_fake(self):
        self.assertIsInstance(payments.get_provider(), payments.FakeProvider)
        self.assertIs(payments.get_provider(), self.provider)
        for name, value in BLANK_STRIPE_SETTINGS.items():
            self.assertEqual(getattr(settings, name), value)

    def test_with_nothing_configured_there_is_no_provider_at_all(self):
        payments.substitute(None)
        self.assertFalse(payments.configured())
        self.assertFalse(payments.payments_on())
        self.assertFalse(payments.test_mode())
        with self.assertRaisesMessage(payments.PaymentError, "not set up"):
            payments.get_provider()

    def test_a_flag_alone_never_builds_a_stripe_provider(self):
        payments.substitute(None)
        with self.settings(PAYMENTS_CONFIGURED=True):  # the key and the secret are still blank
            with self.assertRaises(payments.PaymentError):
                payments.get_provider()
        with self.settings(PAYMENTS_CONFIGURED=True, STRIPE_SECRET_KEY=LIVE_KEY):  # no webhook secret
            with self.assertRaises(payments.PaymentError):
                payments.get_provider()

    def test_a_live_key_with_its_webhook_secret_is_stripe_in_live_mode(self):
        payments.substitute(None)
        with self.settings(PAYMENTS_CONFIGURED=True, STRIPE_SECRET_KEY=LIVE_KEY, STRIPE_WEBHOOK_SECRET=HOOK):
            provider = payments.get_provider()
            self.assertIsInstance(provider, payments.StripeProvider)
            self.assertTrue(provider.livemode)
            self.assertTrue(payments.configured())
            self.assertFalse(payments.test_mode())
            self.assertNotIn(LIVE_KEY, repr(provider))

    def test_a_test_key_is_refused_unless_testing_is_allowed(self):
        payments.substitute(None)
        with self.settings(PAYMENTS_CONFIGURED=True, STRIPE_SECRET_KEY=TEST_KEY, STRIPE_WEBHOOK_SECRET=HOOK):
            with self.assertRaises(payments.PaymentError):
                payments.get_provider()
            self.assertFalse(payments.test_mode())
            with self.settings(STRIPE_ALLOW_TEST=True):
                provider = payments.get_provider()
                self.assertIsInstance(provider, payments.StripeProvider)
                self.assertFalse(provider.livemode)
                self.assertTrue(payments.test_mode())

    def test_local_development_without_a_key_runs_on_the_fake(self):
        payments.substitute(None)
        with self.settings(DEBUG=True):
            self.assertIsInstance(payments.get_provider(), payments.FakeProvider)
            self.assertTrue(payments.configured())
            self.assertFalse(payments.payments_on())  # the administrator's switch is still off
        # A key with no webhook secret is half-configured, on a developer's machine too.
        with self.settings(DEBUG=True, STRIPE_SECRET_KEY=TEST_KEY):
            self.assertFalse(payments.configured())
            with self.assertRaises(payments.PaymentError):
                payments.get_provider()

    def test_substituted_puts_the_previous_provider_back(self):
        other = payments.FakeProvider(livemode=True)
        with payments.substituted(other):
            self.assertIs(payments.get_provider(), other)
        self.assertIs(payments.get_provider(), self.provider)

    def test_a_real_http_call_fails_the_test(self):
        with self.assertRaisesMessage(AssertionError, "tried to reach the network"):
            requests.get("https://api.stripe.com/v1/checkout/sessions/cs_test_x", timeout=1)


# --- The Stripe provider -------------------------------------------------------


class StripeProviderTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.stripe = payments.StripeProvider(TEST_KEY)

    def call(self, reply):
        return mock.patch("billing.payments.requests.request", return_value=reply)

    def test_opening_a_session_sends_exactly_these_fields_and_nothing_else(self):
        args = create_args()
        with self.call(Reply(body=session_body())) as request:
            session = self.stripe.create_session(**args)
        (method, url), sent = request.call_args.args, request.call_args.kwargs
        self.assertEqual((method, url), ("POST", "https://api.stripe.com/v1/checkout/sessions"))
        self.assertEqual(sent["data"], {
            "mode": "payment",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": "6480",
            "line_items[0][price_data][product_data][name]": "Transportation booking, reference #3FA9C2D1",
            "line_items[0][quantity]": "1",
            "client_reference_id": QUOTE_ID,
            "metadata[quote]": QUOTE_ID,
            "payment_intent_data[metadata][quote]": QUOTE_ID,
            "payment_intent_data[description]": "Transportation booking, reference #3FA9C2D1",
            "allowed_payment_method_types[0]": "card",
            "success_url": SUCCESS,
            "cancel_url": CANCEL,
            "expires_at": str(int(args["expires_at"].timestamp())),
        })
        self.assertEqual(sent["headers"], {
            "Authorization": f"Bearer {TEST_KEY}",
            "Stripe-Version": "2026-11-18.endive",
            "Idempotency-Key": f"{QUOTE_ID}-1",
        })
        self.assertEqual(sent["timeout"], (3, 8))
        self.assertIs(sent["allow_redirects"], False)
        self.assertEqual(set(sent), {"data", "headers", "timeout", "allow_redirects"})
        self.assertEqual(session, payments.Session(
            id="cs_test_a1B2c3", url="https://checkout.stripe.com/c/pay/cs_test_a1B2c3#fid",
            expires_at=datetime.fromtimestamp(1900000000, tz=dt_timezone.utc), livemode=False,
        ))

    def test_no_field_can_carry_a_person_an_email_or_the_old_parameter(self):
        fields = payments.StripeProvider.create_params(**{k: v for k, v in create_args().items()
                                                          if k != "idempotency_key"})
        text = " ".join(fields)
        for unwanted in ("customer_email", "payment_method_types[", "phone", "shipping", "tax",
                         "allow_promotion_codes", "receipt_email", "customer"):
            self.assertFalse(any(key.startswith(unwanted) for key in fields), unwanted)
        self.assertNotIn("@", " ".join(fields.values()))
        self.assertIn("allowed_payment_method_types[0]", text)

    def test_a_request_that_could_carry_something_else_is_never_sent(self):
        refused = [
            {"amount_cents": 49}, {"amount_cents": 0}, {"amount_cents": True}, {"amount_cents": Decimal("64.80")},
            {"reference": "#3FA9C2D1 Jamie Chen"}, {"reference": "Jamie Chen"}, {"reference": "#3fa9c2d1"},
            {"reference": "#3FA9C2D1\nwheelchair"}, {"quote_id": "Jamie Chen"}, {"idempotency_key": ""},
            {"success_url": "https://admin.example.test/pay/return/"}, {"cancel_url": ""},
            {"expires_at": datetime(2030, 1, 1)}, {"expires_at": timezone.now() - timedelta(minutes=1)},
        ]
        with mock.patch("billing.payments.requests.request") as request:
            for change in refused:
                with self.subTest(change=change), self.assertRaises(payments.PaymentError):
                    self.stripe.create_session(**create_args(**change))
        request.assert_not_called()

    def test_a_session_that_is_not_stripes_page_or_not_this_keys_mode_is_refused(self):
        for change in ({"url": "https://evil.example.test/c/pay/cs_test_a1B2c3"}, {"url": None},
                       {"url": "http://checkout.stripe.com/c/pay/x"}, {"livemode": True}, {"id": "../x"},
                       {"expires_at": "soon"}):
            with self.subTest(change=change), self.call(Reply(body=session_body(**change))):
                with self.assertLogs("billing.payments", "WARNING"), self.assertRaises(payments.PaymentError):
                    self.stripe.create_session(**create_args())

    def test_reading_a_session_gives_stripes_answer(self):
        body = session_body(status="complete", payment_status="paid", payment_intent="pi_3TEST", currency="USD")
        with self.call(Reply(body=body)) as request:
            state = self.stripe.retrieve_session("cs_test_a1B2c3")
        self.assertEqual(request.call_args.args,
                         ("GET", "https://api.stripe.com/v1/checkout/sessions/cs_test_a1B2c3"))
        self.assertIsNone(request.call_args.kwargs["data"])
        self.assertNotIn("Idempotency-Key", request.call_args.kwargs["headers"])
        self.assertEqual(state, payments.SessionState(
            id="cs_test_a1B2c3", status="complete", payment_status="paid", mode="payment", amount_total=6480,
            currency="usd", payment_intent="pi_3TEST", livemode=False, metadata_quote=QUOTE_ID,
        ))

    def test_missing_parts_of_an_answer_read_as_not_paid_and_not_live(self):
        with self.call(Reply(body={"id": "cs_test_a1B2c3"})):
            state = self.stripe.retrieve_session("cs_test_a1B2c3")
        self.assertEqual((state.status, state.payment_status, state.mode, state.amount_total, state.currency,
                          state.payment_intent, state.livemode, state.metadata_quote),
                         ("", "", "", None, "", None, False, ""))
        with self.call(Reply(body=session_body(payment_intent={"id": "pi_3TEST", "object": "payment_intent"}))):
            self.assertEqual(self.stripe.retrieve_session("cs_test_a1B2c3").payment_intent, "pi_3TEST")

    def test_a_session_id_from_a_browser_can_not_reach_another_address(self):
        with mock.patch("billing.payments.requests.request") as request:
            for bad in ("cs_test_1/../../customers", "../customers", "cs_test_1?expand[]=customer", "", None,
                        "pi_3TEST", "cs_test_1 ", "cs_" + "a" * 300, 42):
                for ask in (self.stripe.retrieve_session, self.stripe.expire_session):
                    with self.subTest(bad=bad), self.assertRaises(payments.PaymentError):
                        ask(bad)
        request.assert_not_called()

    def test_an_answer_about_another_session_is_refused(self):
        with self.call(Reply(body=session_body(id="cs_test_OTHER"))), self.assertLogs("billing.payments", "WARNING"):
            with self.assertRaises(payments.PaymentError):
                self.stripe.retrieve_session("cs_test_a1B2c3")
        with self.call(Reply(body=session_body(amount_total="6480"))), self.assertLogs("billing.payments", "WARNING"):
            with self.assertRaises(payments.PaymentError):
                self.stripe.retrieve_session("cs_test_a1B2c3")

    def test_expiring_a_session(self):
        with self.call(Reply(body=session_body(status="expired", url=None))) as request:
            state = self.stripe.expire_session("cs_test_a1B2c3")
        self.assertEqual(request.call_args.args,
                         ("POST", "https://api.stripe.com/v1/checkout/sessions/cs_test_a1B2c3/expire"))
        self.assertEqual(state.status, "expired")

    def test_a_network_failure_says_nothing_secret(self):
        secret_text = f"HTTPSConnectionPool(host='api.stripe.com'): {TEST_KEY} jamie@example.com 1 Main St"
        for error in (requests.ConnectionError(secret_text), requests.Timeout(secret_text),
                      requests.TooManyRedirects(secret_text)):
            with self.subTest(error=type(error).__name__):
                with mock.patch("billing.payments.requests.request", side_effect=error):
                    with self.assertLogs("billing.payments", "WARNING") as logs:
                        with self.assertRaises(payments.PaymentError) as raised:
                            self.stripe.create_session(**create_args())
                self.assertEqual(str(raised.exception), payments.UNAVAILABLE)
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)
                logged = "\n".join(logs.output)
                self.assertIn("step=create_session", logged)
                self.assertIn(type(error).__name__, logged)
                for secret in (TEST_KEY, "jamie@example.com", "1 Main St", "api.stripe.com"):
                    self.assertNotIn(secret, logged)

    def test_a_refusal_from_stripe_is_logged_as_status_code_and_request_id_only(self):
        body = {"error": {"type": "invalid_request_error", "code": "parameter_unknown",
                          "param": "payment_method_types",
                          "message": f"Received unknown parameter for jamie@example.com with {TEST_KEY}",
                          "request_log_url": "https://dashboard.stripe.com/logs/req_TEST123"}}
        with self.call(Reply(status=400, body=body)), self.assertLogs("billing.payments", "WARNING") as logs:
            with self.assertRaises(payments.PaymentError) as raised:
                self.stripe.create_session(**create_args())
        error = raised.exception
        self.assertEqual((error.step, error.http_status, error.code, error.request_id, error.param),
                         ("create_session", 400, "parameter_unknown", "req_TEST123", "payment_method_types"))
        self.assertEqual(str(error), "Stripe did not accept the request (HTTP 400, parameter_unknown). "
                                     "Stripe request req_TEST123.")
        logged = "\n".join(logs.output)
        self.assertIn("status=400 code=parameter_unknown request_id=req_TEST123", logged)
        for secret in (TEST_KEY, "jamie@example.com", "Received unknown", "dashboard.stripe.com", SUCCESS, QUOTE_ID):
            self.assertNotIn(secret, logged)
            self.assertNotIn(secret, str(error))

    def test_stripe_being_down_too_busy_or_unreadable_is_just_not_available(self):
        replies = (Reply(status=500, body={"error": {"type": "api_error"}}), Reply(status=429, body={"error": {}}),
                   Reply(status=502, text="<html>Bad gateway</html>", request_id=None),
                   Reply(status=200, text="not json"), Reply(status=200, body=["a", "list"]),
                   Reply(status=301, body=None, text=""))
        for reply in replies:
            with self.subTest(status=reply.status_code), self.call(reply):
                with self.assertLogs("billing.payments", "WARNING") as logs:
                    with self.assertRaises(payments.PaymentError) as raised:
                        self.stripe.retrieve_session("cs_test_a1B2c3")
                self.assertEqual(str(raised.exception), payments.UNAVAILABLE)
                self.assertEqual(raised.exception.http_status, reply.status_code)
                self.assertNotIn("Bad gateway", "\n".join(logs.output))

    def test_codes_from_stripe_are_reduced_to_plain_characters(self):
        body = {"error": {"code": "bad code\n<script>jamie@example.com", "param": "line_items[0][price_data]"}}
        with self.call(Reply(status=402, body=body, request_id="req_1\r\nX-Injected: 1")):
            with self.assertLogs("billing.payments", "WARNING") as logs, self.assertRaises(payments.PaymentError) as r:
                self.stripe.retrieve_session("cs_test_a1B2c3")
        self.assertEqual(r.exception.code, "badcodescriptjamieexample.com")
        self.assertEqual(r.exception.request_id, "req_1X-Injected1")
        self.assertEqual(r.exception.param, "line_items[0][price_data]")
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("\n", logs.output[0])

    def test_something_that_is_not_a_secret_key_makes_no_provider(self):
        for key in ("", "pk_live_notakey", "whsec_notasecret", "notakey"):
            with self.subTest(key=key), self.assertRaisesMessage(payments.PaymentError, "not set up"):
                payments.StripeProvider(key)
        self.assertTrue(payments.StripeProvider(LIVE_KEY).livemode)


# --- The in-memory provider ------------------------------------------------------


class FakeProviderTests(PaymentTestCase):
    def test_a_session_opens_unpaid_and_the_same_attempt_gives_the_same_session(self):
        args = create_args()
        first = self.provider.create_session(**args)
        again = self.provider.create_session(**args)
        self.assertEqual(first, again)
        self.assertEqual(first.id, "cs_test_fake0001")
        self.assertTrue(first.url.startswith(payments.CHECKOUT_PREFIX))
        self.assertEqual((first.expires_at, first.livemode), (args["expires_at"], False))
        state = self.provider.retrieve_session(first.id)
        self.assertEqual((state.status, state.payment_status, state.mode, state.amount_total, state.currency,
                          state.payment_intent, state.livemode, state.metadata_quote),
                         ("open", "unpaid", "payment", 6480, "usd", None, False, QUOTE_ID))
        second = self.provider.create_session(**create_args(idempotency_key=f"{QUOTE_ID}-2"))
        self.assertEqual(second.id, "cs_test_fake0002")
        self.assertEqual([step for step, _ in self.provider.calls],
                         ["create_session", "create_session", "retrieve_session", "create_session"])
        self.assertEqual(self.provider.calls[0][1]["reference"], "#3FA9C2D1")

    def test_the_same_attempt_with_other_values_is_refused_as_stripe_refuses_it(self):
        self.provider.create_session(**create_args())
        with self.assertRaises(payments.PaymentError) as raised:
            self.provider.create_session(**create_args(amount_cents=7000))
        self.assertEqual(raised.exception.code, "idempotency_error")

    def test_it_applies_the_same_rules_as_the_real_one_before_opening(self):
        for change in ({"amount_cents": 49}, {"reference": "#3FA9C2D1 Jamie Chen"}, {"quote_id": "x"}):
            with self.subTest(change=change), self.assertRaises(payments.PaymentError):
                self.provider.create_session(**create_args(**change))
        self.assertEqual(self.provider.sessions, {})

    def test_paying_expiring_and_unknown_sessions(self):
        session = self.provider.create_session(**create_args())
        self.provider.pay(session.id)
        state = self.provider.retrieve_session(session.id)
        self.assertEqual((state.status, state.payment_status, state.payment_intent),
                         ("complete", "paid", "pi_fake0001"))
        with self.assertRaises(payments.PaymentError):  # a paid session can't be expired
            self.provider.expire_session(session.id)

        other = self.provider.create_session(**create_args(idempotency_key="k2"))
        self.assertEqual(self.provider.expire_session(other.id).status, "expired")
        with self.assertRaises(payments.PaymentError):
            self.provider.expire_session(other.id)

        with self.assertRaises(payments.PaymentError) as raised:
            self.provider.retrieve_session("cs_test_unknown")
        self.assertEqual((raised.exception.http_status, raised.exception.code), (404, "resource_missing"))
        with self.assertRaises(payments.PaymentError):
            self.provider.retrieve_session("../x")

    def test_an_open_session_closes_by_itself_at_its_expiry_time(self):
        session = self.provider.create_session(**create_args())
        later = timezone.now() + timedelta(minutes=61)
        with mock.patch("billing.payments.timezone.now", return_value=later):
            self.assertEqual(self.provider.retrieve_session(session.id).status, "expired")

    def test_a_test_can_change_the_answer_and_take_the_provider_down(self):
        session = self.provider.create_session(**create_args())
        self.provider.pay(session.id, amount_total=100, livemode=True)
        state = self.provider.retrieve_session(session.id)
        self.assertEqual((state.amount_total, state.livemode), (100, True))
        self.provider.down = True
        for ask in (lambda: self.provider.retrieve_session(session.id),
                    lambda: self.provider.expire_session(session.id),
                    lambda: self.provider.create_session(**create_args(idempotency_key="k3"))):
            with self.assertRaisesMessage(payments.PaymentError, "not available right now"):
                ask()
        self.assertEqual(payments.FakeProvider(livemode=True).create_session(**create_args()).id, "cs_live_fake0001")


# --- Money, names and the pay link ------------------------------------------------


class HelperTests(PaymentTestCase):
    def test_dollars_become_whole_cents_exactly(self):
        for amount, cents in ((Decimal("64.80"), 6480), (Decimal("0.50"), 50), (Decimal("0"), 0), ("19.99", 1999),
                              (Decimal("1234567.89"), 123456789), (3, 300)):
            self.assertEqual(payments.to_cents(amount), cents)
        for bad in (Decimal("0.005"), Decimal("64.801"), Decimal("-1.00")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                payments.to_cents(bad)
        with self.assertRaises(TypeError):
            payments.to_cents(64.8)

    def test_only_fifty_cents_or_more_can_be_paid_by_card(self):
        self.assertTrue(payments.payable(Decimal("0.50")))
        self.assertFalse(payments.payable(Decimal("0.49")))
        self.assertFalse(payments.payable(Decimal("0.00")))

    def test_the_reference_and_the_one_line_stripe_is_given(self):
        request_id = uuid.UUID("3fa9c2d1-0000-4000-8000-000000000000")
        self.assertEqual(payments.reference(request_id), "#3FA9C2D1")
        self.assertEqual(payments.product_name("#3FA9C2D1"), "Transportation booking, reference #3FA9C2D1")

    def test_a_session_stays_open_for_an_hour_to_the_whole_second(self):
        now = datetime(2027, 1, 13, 15, 0, 0, 123456, tzinfo=dt_timezone.utc)
        self.assertEqual(payments.session_expiry(now), datetime(2027, 1, 13, 16, 0, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(payments.session_expiry().microsecond, 0)

    def test_the_pay_link_token_reads_back_and_a_bad_one_reads_as_nothing(self):
        quote = make_quote()
        token = payments.pay_token(quote)
        self.assertRegex(token, r"^[A-Za-z0-9_:-]+$")
        self.assertNotIn(str(quote.pk), token)
        self.assertEqual(payments.read_pay_token(token), str(quote.pk))
        self.assertEqual(payments.pay_url(quote).rsplit("/", 1)[0], f"{settings.WEBSITE_URL}/pay")
        self.assertEqual(payments.read_pay_token(payments.pay_url(quote).rsplit("/", 1)[1]), str(quote.pk))
        for bad in (token[:-1] + ("A" if token[-1] != "A" else "B"), "x" + token, "", "not-a-token", None,
                    signing.dumps(str(quote.pk), salt="another-salt"),
                    signing.dumps("not-a-uuid", salt=payments.PAY_TOKEN_SALT),
                    signing.dumps({"quote": str(quote.pk)}, salt=payments.PAY_TOKEN_SALT)):
            with self.subTest(bad=bad):
                self.assertIsNone(payments.read_pay_token(bad))

    def test_the_pay_link_stops_working_after_thirty_days(self):
        token = payments.pay_token(make_quote())
        almost, past = (time.time() + days * 24 * 3600 for days in (29.9, 30.1))
        with mock.patch("django.core.signing.time.time", return_value=almost):
            self.assertIsNotNone(payments.read_pay_token(token))
        with mock.patch("django.core.signing.time.time", return_value=past):
            self.assertIsNone(payments.read_pay_token(token))

    def test_the_return_addresses_come_from_the_settings(self):
        with self.settings(SITE_URL="https://admin.example.test", WEBSITE_URL="https://www.example.test"):
            self.assertEqual(payments.success_url(), SUCCESS)
            self.assertEqual(payments.cancel_url(), CANCEL)


# --- The tables --------------------------------------------------------------------


class PaymentModelTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.quote = make_quote()

    def test_a_new_payment_is_open_with_nothing_known_yet(self):
        payment = Payment.objects.create(quote=self.quote, amount_cents=6480)
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.attempt, payment.currency), ("open", 1, "usd"))
        for name in ("session_id", "payment_intent_id", "expires_at", "paid_cents", "livemode", "paid_at",
                     "refunded_at", "disputed_at", "office_notified_at", "customer_notified_at"):
            self.assertIsNone(getattr(payment, name), name)
        self.assertEqual((payment.session_url, payment.attention_reason, payment.refunded_cents), ("", "", 0))
        self.assertEqual((payment.amount, payment.paid, payment.refunded),
                         (Decimal("64.80"), None, Decimal("0.00")))
        self.assertFalse(payment.is_paid)
        self.assertEqual(str(payment), "Open (not paid yet) online payment of $64.80")
        self.assertEqual(list(self.quote.payments.all()), [payment])

    def test_one_row_per_attempt_and_stripes_ids_are_unique(self):
        Payment.objects.create(quote=self.quote, amount_cents=6480, attempt=1, session_id="cs_test_1",
                               payment_intent_id="pi_1")
        # Rows with no id yet never collide.
        Payment.objects.create(quote=self.quote, amount_cents=6480, attempt=2)
        Payment.objects.create(quote=self.quote, amount_cents=6480, attempt=3)
        for clash in ({"attempt": 1}, {"attempt": 4, "session_id": "cs_test_1"},
                      {"attempt": 4, "payment_intent_id": "pi_1"},
                      # "None yet" is NULL: an empty string would collide on the second row.
                      {"attempt": 4, "session_id": ""}, {"attempt": 4, "payment_intent_id": ""}):
            with self.subTest(clash=clash), self.assertRaises(IntegrityError), transaction.atomic():
                Payment.objects.create(quote=self.quote, amount_cents=6480, **clash)
        self.assertEqual(Payment.objects.count(), 3)

    def test_a_quote_with_a_payment_can_not_be_deleted(self):
        Payment.objects.create(quote=self.quote, amount_cents=6480)
        with self.assertRaises(ProtectedError):
            self.quote.delete()
        with self.assertRaises(ProtectedError):
            self.quote.trip_request.delete()
        self.assertTrue(Quote.objects.filter(pk=self.quote.pk).exists())

    def test_an_event_is_recorded_once(self):
        PaymentEvent.objects.create(event_id="evt_1", event_type="checkout.session.completed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentEvent.objects.create(event_id="evt_1", event_type="checkout.session.completed")
        self.assertEqual(str(PaymentEvent.objects.get()), "checkout.session.completed (evt_1)")

    def test_is_it_paid(self):
        request = self.quote.trip_request
        newer = make_quote(trip_request=request, amount=Decimal("70.00"))
        open_payment = self.make_payment(self.quote)
        self.assertFalse(payments.quote_paid(self.quote))
        self.assertFalse(payments.request_paid(request))
        self.assertIsNone(payments.paid_payment(request))
        for status in (PaymentStatus.EXPIRED, PaymentStatus.FAILED, PaymentStatus.NEEDS_ATTENTION):
            Payment.objects.filter(pk=open_payment.pk).update(status=status)
            self.assertFalse(payments.request_paid(request), status)
        Payment.objects.filter(pk=open_payment.pk).update(status=PaymentStatus.PAID, paid_at=timezone.now())
        self.assertTrue(payments.quote_paid(self.quote))
        self.assertFalse(payments.quote_paid(newer))
        # A request is paid when any of its quotes is.
        self.assertTrue(payments.request_paid(request))
        self.assertEqual(payments.paid_payment(request), open_payment)
        self.assertFalse(payments.request_paid(make_trip_request()))

    def test_the_helper_that_makes_a_payment_leaves_it_as_the_pay_page_would(self):
        payment = self.make_payment(self.quote)
        self.assertEqual((payment.attempt, payment.amount_cents, payment.session_id, payment.livemode),
                         (1, 6480, "cs_test_fake0001", False))
        self.assertEqual(self.provider.retrieve_session(payment.session_id).metadata_quote, str(self.quote.pk))
        self.assertEqual(self.make_payment(self.quote).attempt, 2)


class PaymentTermsTests(PaymentTestCase):
    def test_with_no_row_the_settings_are_the_safe_defaults_and_nothing_is_written(self):
        terms = payments.terms()
        self.assertEqual(terms, payments.Terms(enabled=False, refund_policy=DEFAULT_REFUND_POLICY,
                                               what_paying_means=DEFAULT_WHAT_PAYING_MEANS))
        self.assertFalse(PaymentTerms.objects.exists())
        self.assertIn("at least 24 hours before", terms.refund_policy)
        self.assertIn("schedules your ride", terms.what_paying_means)

    def test_there_is_one_row_and_it_starts_switched_off(self):
        row = PaymentTerms.load()
        self.assertEqual(PaymentTerms.load().pk, row.pk)
        self.assertEqual(PaymentTerms.objects.count(), 1)
        self.assertEqual((row.enabled, row.refund_policy, row.what_paying_means),
                         (False, DEFAULT_REFUND_POLICY, DEFAULT_WHAT_PAYING_MEANS))
        self.assertFalse(payments.terms().enabled)

    def test_the_administrators_words_are_used_and_a_blank_text_falls_back(self):
        PaymentTerms.objects.create(enabled=True, refund_policy="  Our own refund rule.  ", what_paying_means="  ")
        self.assertEqual(payments.terms(), payments.Terms(enabled=True, refund_policy="Our own refund rule.",
                                                          what_paying_means=DEFAULT_WHAT_PAYING_MEANS))

    def test_payment_is_on_only_when_configured_and_switched_on(self):
        self.assertFalse(payments.payments_on())  # the default: nothing configured, switch off
        PaymentTerms.objects.create(enabled=True)
        self.assertFalse(payments.payments_on())  # switched on, but the server isn't configured
        with self.settings(PAYMENTS_CONFIGURED=True):
            self.assertTrue(payments.payments_on())
            PaymentTerms.objects.update(enabled=False)
            self.assertFalse(payments.payments_on())  # configured, but the switch is off

    def test_the_test_helper_switches_it_on(self):
        row = self.payments_on(refund_policy="Reworded.")
        self.assertTrue(payments.payments_on())
        self.assertEqual((row.enabled, payments.terms().refund_policy), (True, "Reworded."))
        self.assertTrue(payments.test_mode())  # the fake provider only ever moves test money

    def test_an_unconfigured_server_never_reads_the_database_to_say_off(self):
        with self.assertNumQueries(0):
            self.assertFalse(payments.payments_on())

    def test_missing_tables_mean_off_and_not_paid_and_leave_the_page_working(self):
        """Code live before its database update: the new tables aren't there yet."""
        quote = make_quote()
        with connection.cursor() as cursor:  # undone with the test's transaction
            cursor.execute("ALTER TABLE billing_paymentterms RENAME TO billing_paymentterms_gone")
            cursor.execute("ALTER TABLE billing_payment RENAME TO billing_payment_gone")
        with self.settings(PAYMENTS_CONFIGURED=True), self.assertLogs("billing.payments", "WARNING") as logs:
            self.assertEqual(payments.terms(), payments.Terms())
            self.assertFalse(payments.payments_on())
            self.assertFalse(payments.quote_paid(quote))
            self.assertFalse(payments.request_paid(quote.trip_request))
            self.assertIsNone(payments.paid_payment(quote.trip_request))
        self.assertIn("A payment table is missing", logs.output[0])
        # The surrounding transaction still works: the page that asked carries on.
        self.assertTrue(Quote.objects.filter(pk=quote.pk).exists())
        self.assertEqual(payments.safe_read(lambda: 1 / 1, None), 1)

    def test_any_other_database_fault_is_never_read_as_off_or_not_paid(self):
        quote = make_quote()

        def broken(*args, **kwargs):
            raise OperationalError("could not connect to server")

        with mock.patch("django.db.models.query.QuerySet.exists", broken):
            with self.assertRaises(OperationalError):
                payments.quote_paid(quote)
        with mock.patch("django.db.models.query.QuerySet.first", broken):
            with self.assertRaises(OperationalError):
                payments.terms()
            with self.assertRaises(OperationalError):
                payments.request_paid(quote.trip_request)

    def test_postgres_says_a_table_is_missing_with_its_own_code(self):
        class Cause(Exception):
            sqlstate = "42P01"  # undefined_table

        class OtherCause(Exception):
            sqlstate = "40001"  # serialization_failure

        def error(cause):
            exc = ProgrammingError('relation "billing_payment" does not exist')
            exc.__cause__ = cause
            return exc

        self.assertTrue(payments._table_missing(error(Cause())))
        self.assertFalse(payments._table_missing(error(OtherCause())))
        self.assertTrue(payments._table_missing(OperationalError("no such table: billing_payment")))
        self.assertFalse(payments._table_missing(OperationalError("database is locked")))


# --- The admin pages -----------------------------------------------------------------


class PaymentAdminTests(PaymentTestCase):
    def login(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")
        return user

    def paid(self):
        quote = make_quote()
        payment = self.make_payment(quote, status=PaymentStatus.PAID, paid_cents=6480, paid_at=timezone.now(),
                                    payment_intent_id="pi_fake0001")
        return quote, payment

    def test_an_administrator_sees_payments_and_can_only_read_them(self):
        self.login("Administrator")
        quote, payment = self.paid()
        reference = payments.reference(quote.trip_request_id)
        page = self.client.get("/admin/billing/payment/")
        self.assertContains(page, reference)
        self.assertContains(page, "$64.80")
        self.assertNotContains(page, "Stripe TEST MODE")  # nothing is configured
        detail = self.client.get(f"/admin/billing/payment/{payment.pk}/change/")
        self.assertContains(detail, reference)
        self.assertContains(detail, "cs_test_fake0001")
        self.assertContains(detail, "Stripe TEST payment (no real money)")
        self.assertContains(detail, f"/admin/transportation/triprequest/{quote.trip_request_id}/change/")
        self.assertNotContains(detail, 'name="_save"')
        self.assertNotContains(detail, payment.session_url)  # the pay page's address is not shown
        self.assertTrue(AuditLog.objects.filter(action="viewed", resource_type="Payment",
                                                resource_id=str(payment.pk)).exists())
        self.assertEqual(self.client.get("/admin/billing/payment/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/billing/payment/{payment.pk}/change/",
                                          {"status": "open"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/billing/payment/{payment.pk}/delete/",
                                          {"post": "yes"}).status_code, 403)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PAID)

    def test_search_by_reference_and_filter_by_status(self):
        self.login("Administrator")
        quote, payment = self.paid()
        other = make_quote(amount=Decimal("12.34"))
        self.make_payment(other)
        reference = payments.reference(quote.trip_request_id)
        other_reference = payments.reference(other.trip_request_id)

        found = self.client.get("/admin/billing/payment/", {"q": reference})  # typed with its "#"
        self.assertContains(found, reference)
        self.assertNotContains(found, other_reference)
        self.assertContains(self.client.get("/admin/billing/payment/", {"q": reference[1:].lower()}), reference)
        self.assertContains(self.client.get("/admin/billing/payment/", {"q": "pi_fake0001"}), reference)
        self.assertNotContains(self.client.get("/admin/billing/payment/", {"q": "#00000000"}), reference)

        only_open = self.client.get("/admin/billing/payment/", {"status__exact": "open"})
        self.assertContains(only_open, other_reference)
        self.assertNotContains(only_open, reference)

    def test_a_dispatcher_sees_neither_page(self):
        self.login("Dispatcher")
        _, payment = self.paid()
        for url in ("/admin/billing/payment/", f"/admin/billing/payment/{payment.pk}/change/",
                    "/admin/billing/paymentterms/", "/admin/billing/paymentterms/1/change/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertFalse(PaymentTerms.objects.exists())  # being refused made no settings row
        home = self.client.get("/admin/")
        self.assertNotContains(home, "/admin/billing/payment/")
        self.assertNotContains(home, "/admin/billing/paymentterms/")

    def test_the_sidebar_shows_both_pages_to_an_administrator(self):
        self.login("Administrator")
        home = self.client.get("/admin/")
        self.assertContains(home, "/admin/billing/payment/")
        self.assertContains(home, "/admin/billing/paymentterms/")
        self.assertContains(home, "Payment settings")

    def test_payment_settings_is_one_page_that_an_administrator_can_change(self):
        admin = self.login("Administrator")
        listing = self.client.get("/admin/billing/paymentterms/")
        row = PaymentTerms.objects.get()  # opening the page made the one row
        self.assertContains(listing, f"/admin/billing/paymentterms/{row.pk}/change/")
        self.assertContains(listing, "Online payment is OFF")
        self.client.get("/admin/billing/paymentterms/")
        self.assertEqual(PaymentTerms.objects.count(), 1)

        page = self.client.get(f"/admin/billing/paymentterms/{row.pk}/change/")
        self.assertContains(page, "Online payment is OFF. Stripe is not set up on the server yet")
        self.assertContains(page, "at least 24 hours before")
        self.assertContains(page, 'name="_save"')

        saved = self.client.post(f"/admin/billing/paymentterms/{row.pk}/change/", {
            "enabled": "on", "refund_policy": "Our own refund rule.", "what_paying_means": row.what_paying_means,
        })
        self.assertEqual(saved.status_code, 302)
        row.refresh_from_db()
        self.assertEqual((row.enabled, row.refund_policy), (True, "Our own refund rule."))
        entry = AuditLog.objects.get(action="changed", resource_type="PaymentTerms")
        self.assertEqual((entry.actor, entry.resource_id), (admin, str(row.pk)))
        self.assertEqual(entry.after, {"enabled": "True", "refund_policy": "Our own refund rule."})
        self.assertFalse(payments.payments_on())  # switched on, yet the server is not configured

        self.assertEqual(self.client.get("/admin/billing/paymentterms/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/billing/paymentterms/{row.pk}/delete/",
                                          {"post": "yes"}).status_code, 403)
        self.assertEqual(PaymentTerms.objects.count(), 1)

    def test_the_settings_page_says_whether_payment_is_offered_right_now(self):
        self.login("Administrator")
        row = PaymentTerms.load()
        url = f"/admin/billing/paymentterms/{row.pk}/change/"
        with self.settings(PAYMENTS_CONFIGURED=True):
            self.assertContains(self.client.get(url), "Online payment is OFF. Price emails ask the customer to call")
        self.payments_on()
        self.assertContains(self.client.get(url), "Online payment is ON. Price emails carry a payment link.")

    def test_the_payment_pages_say_test_mode_while_the_server_holds_a_test_key(self):
        self.login("Administrator")
        _, payment = self.paid()
        row = self.payments_on()  # the fake provider stands for a test-mode key
        for url in ("/admin/billing/payment/", f"/admin/billing/payment/{payment.pk}/change/",
                    f"/admin/billing/paymentterms/{row.pk}/change/"):
            self.assertContains(self.client.get(url), "Stripe TEST MODE", msg_prefix=url)
        with payments.substituted(payments.FakeProvider(livemode=True)):
            self.assertNotContains(self.client.get("/admin/billing/payment/"), "Stripe TEST MODE")

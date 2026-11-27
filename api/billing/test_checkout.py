"""
Online payment, the customer's side (ADR 0050): the pay link in the price
email, the page behind it, what Stripe reports back (the return page, the
webhook, the daily check), closing payments that must no longer be paid,
and what the office sees.

No test here holds a real key or reaches the network: `PaymentTestMixin`
puts the in-memory provider in Stripe's place and makes a real HTTP call
fail the test. Every name, address and amount is made up.
"""

import hashlib
import hmac
import json
import re
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.db import connection
from django.template import engines
from django.test import Client, SimpleTestCase, override_settings
from django.utils import timezone
from django.utils.html import escape

import notifications
from accounts.dashboard import build_dashboard
from accounts.otp_test_utils import login_with_otp
from accounts.test_dashboard import panel, staff
from audit.models import AuditLog
from notifications import services as notification_services
from notifications.models import NotificationLog, NotificationType
from notifications.services import NotificationService
from notifications.tests import FailingEmailProvider
from payers.models import Payer
from riders import services as rider_services
from transportation.models import TripRequest, TripRequestStatus
from trips.models import Trip
from trips.tests import make_passenger

from . import checkout, pay_views, payments, pricing, quotes
from .models import Payment, PaymentEvent, PaymentStatus, PaymentTerms, Quote
from .payment_testing import PaymentTestMixin
from .test_quotes import CONFIRMED, QuoteTestCase

# A made-up value in the shape of Stripe's, too short to be a real secret.
HOOK = "whsec_test_notasecret"
WEBHOOK = "/api/v1/payments/stripe/webhook/"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
# The generic refusal as it appears in the page's HTML (its apostrophe is escaped there).
GENERIC = escape(pay_views.GENERIC)


def signature(body: bytes, *, secret: str = HOOK, t: int | None = None, scheme: str = "v1") -> str:
    """A Stripe-Signature header, worked out here from the documented rule and nothing else."""
    t = int(time.time()) if t is None else t
    digest = hmac.new(secret.encode("utf-8"), f"{t}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return f"t={t},{scheme}={digest}"


class FlowTestCase(PaymentTestMixin, QuoteTestCase):
    """A private-pay request, the fake provider, and the customer's and Stripe's moves."""

    def setUp(self):
        super().setUp()
        hook = override_settings(STRIPE_WEBHOOK_SECRET=HOOK)
        hook.enable()
        self.addCleanup(hook.disable)

    def sent(self, request=None, *, miles="12.4", **kwargs) -> Quote:
        """A price emailed for a request: $64.80 for the default request and miles."""
        request = request or self.request()
        with self.captureOnCommitCallbacks(execute=True):
            return quotes.send(request, miles=Decimal(miles), wait_minutes=kwargs.pop("wait_minutes", 0), user=None,
                               **CONFIRMED, **kwargs)

    @staticmethod
    def pay_path(quote) -> str:
        return f"/pay/{payments.pay_token(quote)}/"

    def press(self, quote, client=None):
        """The customer presses "Continue to secure payment"."""
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.client).post(self.pay_path(quote))

    def opened(self, quote=None) -> Payment:
        """An open payment, made the way a customer makes one: through the page."""
        quote = quote or self.sent()
        response = self.press(quote)
        self.assertEqual(response.status_code, 303, response.content)
        return quote.payments.order_by("-attempt").first()

    def apply(self, payment, **kwargs) -> str:
        """Ask "Stripe" about the payment's session and record the answer, emails included."""
        with self.captureOnCommitCallbacks(execute=True):
            return checkout.apply_session(self.provider.retrieve_session(payment.session_id), **kwargs)

    def paid(self, quote=None) -> Payment:
        payment = self.opened(quote)
        self.provider.pay(payment.session_id)
        self.assertEqual(self.apply(payment), PaymentStatus.PAID)
        payment.refresh_from_db()
        return payment

    def created(self) -> list[dict]:
        """Every "open a session" the provider was asked for."""
        return [what for step, what in self.provider.calls if step == "create_session"]

    def asked(self, step: str) -> int:
        return sum(1 for name, _ in self.provider.calls if name == step)

    def event(self, kind: str, thing: dict, *, event_id: str = "evt_test_0001", livemode: bool = False) -> bytes:
        return json.dumps({"id": event_id, "object": "event", "type": kind, "livemode": livemode,
                           "data": {"object": thing}}).encode("utf-8")

    def deliver(self, body: bytes, header: str | None = None, client=None):
        """Stripe posts a report to the webhook."""
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.client).post(
                WEBHOOK, data=body, content_type="application/json",
                headers={"Stripe-Signature": signature(body) if header is None else header},
            )

    def session_event(self, payment, kind="checkout.session.completed", **extra) -> bytes:
        return self.event(kind, {"id": payment.session_id, "object": "checkout.session"}, **extra)

    @staticmethod
    def bodies(message) -> str:
        """The text and the HTML of an email, together."""
        return message.body + "".join(str(alternative[0]) for alternative in message.alternatives)

    def assertPrivate(self, text: str):
        """Nothing that identifies the rider or the ride: no name, address, date, time or service level."""
        for secret in ("Jamie", "Chen", "Main St", "Clinic Rd", "Fort Worth", "Jan 13", "January", "13, 2027",
                       "2027-01-13", "9:00", "09:00", "heelchair", "mbulatory", "817-555"):
            self.assertNotIn(secret, text)


# --- The webhook's signature ---------------------------------------------------


class SignatureTests(SimpleTestCase):
    BODY = b'{"id":"evt_test_vector","type":"checkout.session.completed"}'
    T = 1900000000

    def ok(self, header, body=None, *, secrets=(HOOK,), now=None):
        return checkout.verify_signature(self.BODY if body is None else body, header, list(secrets),
                                         now=self.T if now is None else now)

    def test_a_known_vector(self):
        """HMAC-SHA256 of "<t>.<body>" under the whole whsec_ string: this digest was worked out by hand once."""
        digest = "5c3b8756231d1e5bb9e7ca172663935c85c849bc170c52e55aac322f2833129d"
        computed = hmac.new(HOOK.encode(), b"1900000000." + self.BODY, hashlib.sha256).hexdigest()
        self.assertEqual(computed, digest)
        self.assertTrue(self.ok(f"t=1900000000,v1={digest}"))
        self.assertEqual(signature(self.BODY, t=self.T), f"t=1900000000,v1={digest}")

    def test_one_changed_byte_a_wrong_secret_or_a_forged_digest_is_refused(self):
        good = signature(self.BODY, t=self.T)
        self.assertTrue(self.ok(good))
        self.assertFalse(self.ok(good, self.BODY.replace(b"completed", b"complEted")))
        self.assertFalse(self.ok(signature(self.BODY, t=self.T, secret="whsec_test_someoneelse")))
        self.assertFalse(self.ok(f"t={self.T},v1={'0' * 64}"))
        self.assertFalse(self.ok(good[:-1]))  # a digest cut short
        # Signed for another moment: the time is part of what is signed.
        self.assertFalse(self.ok(good.replace(f"t={self.T}", f"t={self.T + 1}")))

    def test_five_minutes_either_way_and_no_more(self):
        good = signature(self.BODY, t=self.T)
        self.assertTrue(self.ok(good, now=self.T + 300))
        self.assertFalse(self.ok(good, now=self.T + 301))  # stale: a replayed report
        self.assertTrue(self.ok(good, now=self.T - 300))
        self.assertFalse(self.ok(good, now=self.T - 301))  # dated in the future

    def test_any_v1_under_any_configured_secret_is_enough(self):
        good = signature(self.BODY, t=self.T)
        digest = good.split("v1=")[1]
        self.assertTrue(self.ok(f"t={self.T},v1={'0' * 64},v1={digest}"))  # two v1, one valid
        self.assertTrue(self.ok(f"t={self.T},v1={digest},v1={'0' * 64}"))
        # While a secret is being rolled the server holds two.
        rolled = signature(self.BODY, t=self.T, secret="whsec_test_thenewone")
        self.assertTrue(self.ok(rolled, secrets=(HOOK, "whsec_test_thenewone")))
        self.assertTrue(self.ok(good, secrets=("whsec_test_thenewone", HOOK)))
        self.assertFalse(self.ok(rolled, secrets=(HOOK,)))

    def test_v0_and_unknown_entries_are_ignored(self):
        good = signature(self.BODY, t=self.T)
        digest = good.split("v1=")[1]
        self.assertFalse(self.ok(f"t={self.T},v0={digest}"))  # the right digest under the wrong scheme
        self.assertFalse(self.ok(f"t={self.T}"))  # no v1 at all
        self.assertTrue(self.ok(f"t={self.T},v0={'0' * 64},v1={digest},extra=1,novalue, v9=x=y"))
        # Each entry is split on its first "=" only.
        self.assertFalse(self.ok(f"t={self.T},v1={digest}=tail"))
        self.assertTrue(self.ok(f" t={self.T} , v1={digest} "))

    def test_a_header_that_is_not_a_signature_is_refused_not_an_error(self):
        digest = signature(self.BODY, t=self.T).split("v1=")[1]
        for header in ("", "garbage", f"v1={digest}", f"t=,v1={digest}", f"t=soon,v1={digest}",
                       f"t=-{self.T},v1={digest}", f"t={self.T}.0,v1={digest}", "t=1900000000,v1=é",
                       f"t={'9' * 40},v1={digest}"):
            self.assertFalse(self.ok(header), header)
        self.assertFalse(checkout.verify_signature(self.BODY, signature(self.BODY, t=self.T), [], now=self.T))
        self.assertFalse(checkout.verify_signature("text", signature(self.BODY, t=self.T), [HOOK], now=self.T))
        self.assertFalse(checkout.verify_signature(self.BODY, None, [HOOK], now=self.T))

    def test_the_signature_covers_the_raw_bytes_whatever_they_are(self):
        raw = b"\xff\xfe not text at all \x00"
        self.assertTrue(self.ok(signature(raw, t=self.T), raw))
        self.assertFalse(self.ok(signature(raw, t=self.T), raw + b" "))


# --- The pay link in the price email --------------------------------------------


class PayLinkEmailTests(FlowTestCase):
    def test_with_payment_off_the_email_is_the_one_from_before_online_payment_byte_for_byte(self):
        """
        Off is the default. The email must be exactly the template without
        its pay block: the block may add nothing, not even a blank line.
        """
        rendered = []
        real = notification_services.render_to_string

        def recording(name, context=None, *args, **kwargs):
            rendered.append((name, dict(context or {})))
            return real(name, context, *args, **kwargs)

        with mock.patch.object(notification_services, "render_to_string", recording):
            self.sent()
        message = mail.outbox[-1]
        folder = Path(notifications.__file__).parent / "templates"
        for name, body in (("notifications/customer_quote.txt", message.body),
                           ("notifications/customer_quote.html", message.alternatives[0][0])):
            context = next(c for n, c in rendered if n == name)
            self.assertNotIn("pay", context)
            source = (folder / name).read_text(encoding="utf-8")
            without, removed = re.subn(r"{% if pay %}.*?{% endif %}", "", source, flags=re.S)
            self.assertEqual(removed, 1, name)
            self.assertNotIn("pay.", without)
            self.assertEqual(body, engines["django"].from_string(without).render(context), name)
            self.assertNotIn("/pay/", body)
            self.assertNotIn("Pay securely online", body)
            self.assertNotIn("refund", body.lower())
            self.assertIn("call", body)
            self.assertIn("Your ride is not booked yet.", body)
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(PaymentTerms.objects.exists())  # sending a price made no settings row

    def test_half_on_is_off(self):
        # The switch on, the server not configured.
        row = PaymentTerms.load()
        row.enabled = True
        row.save()
        self.sent()
        off = self.bodies(mail.outbox[-1])
        self.assertNotIn("/pay/", off)
        # The server configured, the switch off.
        row.enabled = False
        row.save()
        with self.settings(PAYMENTS_CONFIGURED=True):
            self.sent(miles="20")
        self.assertNotIn("/pay/", self.bodies(mail.outbox[-1]))
        self.assertEqual(self.provider.calls, [])

    def test_with_payment_on_the_email_carries_the_link_and_the_two_texts(self):
        self.payments_on(refund_policy="Our own refund rule.", what_paying_means="What paying means, in our words.")
        quote = self.sent()
        message = mail.outbox[-1]
        url = f"{settings.WEBSITE_URL}/pay/{payments.pay_token(quote)}"
        token = url.rsplit("/", 1)[1]
        self.assertEqual(payments.read_pay_token(token), str(quote.pk))
        self.assertRegex(token, r"^[A-Za-z0-9_:-]+$")
        html = message.alternatives[0][0]
        for body in (message.body, html):
            self.assertIn(url, body)
            self.assertIn("This link works until your ride's pickup time.", body.replace("&#x27;", "'"))
            self.assertIn("Our own refund rule.", body)
            self.assertIn("What paying means, in our words.", body)
            self.assertIn("Your ride is not booked yet.", body)  # calling stays the alternative
            self.assertIn("817-766-9228", body)
            self.assertNotIn(checkout.WAITING_RULE, body)  # nobody waits on this ride
            self.assertPrivate(body.replace(token, ""))  # the token is random letters: judged on its own above
        self.assertIn("Pay securely online", html)
        self.assertIn(f'href="{url}"', html)
        self.assertNotIn("pay", message.subject.lower())
        # Sending the email asked Stripe nothing: a page is only opened when the customer presses the button.
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())

    def test_a_ride_with_a_waiting_driver_also_says_how_extra_waiting_is_billed(self):
        self.payments_on()
        self.sent(self.request(return_ride="wait"), miles="5", wait_minutes=45)
        message = mail.outbox[-1]
        self.assertIn(checkout.WAITING_RULE, message.body)
        self.assertIn(checkout.WAITING_RULE, message.alternatives[0][0])
        for other in ("scheduled", "will_call"):
            self.sent(self.request(return_ride=other, return_datetime=None), miles="5")
            self.assertNotIn(checkout.WAITING_RULE, self.bodies(mail.outbox[-1]))

    def test_a_total_under_fifty_cents_gets_no_link(self):
        self.payments_on()
        self.card.base_ambulatory = Decimal("0.49")
        self.card.per_mile = Decimal("0")
        self.card.save()
        quote = self.sent(self.request(service_type="ambulatory"))
        self.assertEqual(quote.amount, Decimal("0.49"))
        self.assertNotIn("/pay/", self.bodies(mail.outbox[-1]))
        # And the link, were someone to build it, is refused.
        self.assertContains(self.client.get(self.pay_path(quote)), GENERIC)
        # Fifty cents is the least Stripe takes: from there the link is offered.
        self.card.base_ambulatory = Decimal("0.50")
        self.card.save()
        self.sent(self.request(service_type="ambulatory"))
        self.assertIn("/pay/", self.bodies(mail.outbox[-1]))

    def test_a_newer_price_closes_the_payment_page_of_the_older_one(self):
        self.payments_on()
        request = self.request()
        first = self.sent(request)
        payment = self.opened(first)
        second = self.sent(request, miles="20")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.EXPIRED)
        self.assertEqual(self.provider.sessions[payment.session_id]["status"], "expired")  # closed at Stripe too
        # The old link says why; the new one works.
        self.assertContains(self.client.get(self.pay_path(first)), checkout.REPLACED)
        self.assertContains(self.press(first), checkout.REPLACED)
        self.assertEqual(self.press(second).status_code, 303)

    def test_the_older_page_is_closed_here_even_when_stripe_can_not_be_told(self):
        self.payments_on()
        request = self.request()
        payment = self.opened(self.sent(request))
        self.provider.down = True
        self.sent(request, miles="20")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.EXPIRED)
        self.assertEqual(self.provider.sessions[payment.session_id]["status"], "open")  # Stripe never heard
        # The customer pays on the page Stripe still shows: real money for a replaced price.
        self.provider.down = False
        self.provider.pay(payment.session_id)
        self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)

    def test_a_price_whose_email_failed_replaces_nothing(self):
        self.payments_on()
        request = self.request()
        first = self.sent(request)
        payment = self.opened(first)
        failed = self.sent(request, miles="20", notifier=NotificationService(email_provider=FailingEmailProvider()))
        self.assertFalse(failed.delivered)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.OPEN)
        self.assertEqual(self.press(first).status_code, 303)  # the customer still holds the first price
        self.assertContains(self.client.get(self.pay_path(failed)), GENERIC)

    def test_a_new_price_is_refused_once_the_ride_is_paid(self):
        self.payments_on()
        request = self.request()
        payment = self.paid(self.sent(request))
        emails = len(mail.outbox)
        with self.assertRaisesMessage(pricing.PricingError, quotes.ALREADY_PAID):
            self.sent(request, miles="20")
        with self.assertRaisesMessage(pricing.PricingError, "Refund it in Stripe first"):
            self.sent(request)  # the same price again is refused too
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (1, emails))
        # Refunded in full in Stripe: the price can change again.
        self.assertTrue(checkout.record_refund(payment.payment_intent_id, payment.paid_cents))
        self.assertFalse(payments.request_paid(request))
        second = self.sent(request, miles="20")
        self.assertTrue(second.delivered)
        self.assertEqual(self.press(second).status_code, 303)


# --- The page behind the link ------------------------------------------------------


class PayPageTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on(refund_policy="Our own refund rule.", what_paying_means="What paying means, in our words.")

    def test_opening_the_link_shows_the_price_and_changes_nothing(self):
        quote = self.sent()
        audit, logs = AuditLog.objects.count(), NotificationLog.objects.count()
        for _ in range(3):  # a mail scanner, then the customer, twice
            page = self.client.get(self.pay_path(quote))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, payments.reference(quote.trip_request_id))
        self.assertContains(page, "$64.80")
        self.assertContains(page, "What paying means, in our words.")
        self.assertContains(page, "Our own refund rule.")
        self.assertContains(page, "Continue to secure payment")
        self.assertContains(page, 'method="post"')
        self.assertContains(page, "csrfmiddlewaretoken")
        self.assertNotContains(page, checkout.WAITING_RULE)
        self.assertNotContains(page, "/admin/")
        # The form's own token is random letters, so it is left out of this check.
        self.assertPrivate(re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", page.content.decode()))
        for name, value in HEADERS.items():
            self.assertEqual(page[name], value)
        # Nothing was asked of Stripe and nothing was written.
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())
        self.assertEqual((AuditLog.objects.count(), NotificationLog.objects.count()), (audit, logs))

    def test_the_page_of_a_ride_with_a_waiting_driver_says_how_extra_waiting_is_billed(self):
        quote = self.sent(self.request(return_ride="wait"), miles="5", wait_minutes=45)
        self.assertContains(self.client.get(self.pay_path(quote)), checkout.WAITING_RULE)

    def test_the_button_opens_one_page_at_stripe_and_sends_the_customer_there(self):
        quote = self.sent()
        before = timezone.now()
        response = self.press(quote)
        self.assertEqual(response.status_code, 303)
        payment = Payment.objects.get()
        self.assertEqual(response["Location"], payment.session_url)
        self.assertTrue(response["Location"].startswith("https://checkout.stripe.com/"))
        for name, value in HEADERS.items():
            self.assertEqual(response[name], value)
        self.assertEqual((payment.quote, payment.attempt, payment.status, payment.amount_cents, payment.currency),
                         (quote, 1, PaymentStatus.OPEN, 6480, "usd"))
        self.assertEqual((payment.livemode, payment.paid_cents, payment.payment_intent_id), (False, None, None))
        self.assertGreater(payment.expires_at, before + timedelta(minutes=59))
        self.assertLessEqual(payment.expires_at, timezone.now() + timedelta(minutes=60))
        # Exactly this went to "Stripe", and nothing about the rider.
        (asked,) = self.created()
        self.assertEqual(asked, {
            "amount_cents": 6480, "reference": payments.reference(quote.trip_request_id), "quote_id": str(quote.pk),
            "idempotency_key": f"{quote.pk}-1", "success_url": payments.success_url(),
            "cancel_url": payments.cancel_url(), "expires_at": payment.expires_at,
        })

    def test_a_second_press_or_a_second_browser_gets_the_same_page(self):
        quote = self.sent()
        first = self.press(quote)
        second = self.press(quote)
        third = self.press(quote, Client())
        self.assertEqual({first["Location"], second["Location"], third["Location"]}, {first["Location"]})
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(len(self.created()), 1)  # Stripe was asked once
        self.assertEqual(len(self.provider.sessions), 1)

    def test_two_browsers_pressing_at_the_same_moment_open_one_page(self):
        """The second press arrives while Stripe is still answering the first."""
        quote = self.sent()
        nested = []

        class WhileStripeAnswers(payments.FakeProvider):
            def create_session(inner, **kwargs):
                session = super().create_session(**kwargs)
                if not nested:
                    nested.append(None)
                    nested[0] = checkout.start(quote.pk)
                return session

        payments.substitute(WhileStripeAnswers())
        outer = checkout.start(quote.pk)
        self.assertTrue(outer.url)
        self.assertEqual(outer.url, nested[0].url)
        payment = Payment.objects.get()
        self.assertEqual((payment.attempt, payment.session_url), (1, outer.url))
        self.assertEqual(len(payments.get_provider().sessions), 1)

    def test_the_button_needs_the_pages_own_token(self):
        quote = self.sent()
        strict = Client(enforce_csrf_checks=True)
        page = strict.get(self.pay_path(quote))
        refused = strict.post(self.pay_path(quote))
        self.assertEqual(refused.status_code, 403)
        for name, value in HEADERS.items():
            self.assertEqual(refused[name], value)
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.provider.calls, [])
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        accepted = strict.post(self.pay_path(quote), {"csrfmiddlewaretoken": token})
        self.assertEqual(accepted.status_code, 303)

    def test_other_methods_are_refused(self):
        quote = self.sent()
        for method in ("put", "delete", "patch"):
            response = getattr(self.client, method)(self.pay_path(quote))
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_forged_damaged_or_old_link_gets_the_one_generic_page(self):
        quote = self.sent()
        good = payments.pay_token(quote)
        forged = good[:-3] + ("aaa" if not good.endswith("aaa") else "bbb")
        for token in (forged, "not-a-token", str(quote.pk), good + "x"):
            for response in (self.client.get(f"/pay/{token}/"), self.client.post(f"/pay/{token}/")):
                self.assertEqual(response.status_code, 404)
                self.assertContains(response, GENERIC, status_code=404)
                self.assertNotContains(response, "$64.80", status_code=404)
                self.assertNotContains(response, "Continue to secure payment", status_code=404)
                self.assertEqual(response["Cache-Control"], "no-store")
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 31 * 24 * 3600):
            self.assertEqual(self.client.get(f"/pay/{good}/").status_code, 404)
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())

    def refused(self, quote, reason=None):
        """Both the page and the button refuse, with the generic sentence and at most this one reason."""
        for response in (self.client.get(self.pay_path(quote)), self.press(quote)):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, GENERIC)
            self.assertNotContains(response, "Continue to secure payment")
            self.assertNotContains(response, "$64.80")
            for known in (checkout.ALREADY_PAID, checkout.REPLACED, checkout.TOO_LATE, checkout.CANCELLED):
                if known == reason:
                    self.assertContains(response, known)
                else:
                    self.assertNotContains(response, known)
            self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_cancelled_or_declined_request_can_not_be_paid(self):
        quote = self.sent()
        TripRequest.objects.filter(pk=quote.trip_request_id).update(status=TripRequestStatus.CANCELLED)
        self.refused(quote, checkout.CANCELLED)
        TripRequest.objects.filter(pk=quote.trip_request_id).update(status=TripRequestStatus.DECLINED)
        self.refused(quote)  # no reason given
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())

    def test_a_ride_whose_pickup_time_has_passed_can_not_be_paid(self):
        quote = self.sent()
        TripRequest.objects.filter(pk=quote.trip_request_id).update(
            requested_datetime=timezone.now() - timedelta(minutes=1))
        self.refused(quote, checkout.TOO_LATE)
        self.assertEqual(self.provider.calls, [])

    def test_a_price_that_was_never_delivered_or_is_not_the_newest_can_not_be_paid(self):
        request = self.request()
        failed = self.sent(request, notifier=NotificationService(email_provider=FailingEmailProvider()))
        self.refused(failed)
        first = self.sent(request, miles="15")
        self.sent(request, miles="20")
        self.refused(first, checkout.REPLACED)
        self.assertEqual(self.provider.calls, [])

    def test_a_medicaid_or_broker_rider_is_never_sent_to_pay(self):
        quote = self.sent()
        broker = Payer.objects.create(name="TEST broker", payer_type="broker")
        request = quote.trip_request
        request.passenger = make_passenger(payer=broker)
        request.save()
        self.refused(quote)
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())

    def test_with_payment_switched_off_an_old_link_stops_working(self):
        quote = self.sent()
        PaymentTerms.objects.update(enabled=False)
        self.refused(quote)
        PaymentTerms.objects.update(enabled=True)
        with self.settings(PAYMENTS_CONFIGURED=False):
            self.refused(quote)
        self.assertEqual(self.provider.calls, [])

    def test_a_paid_ride_can_not_be_paid_again(self):
        quote = self.sent()
        self.paid(quote)
        calls = len(self.provider.calls)
        self.refused(quote, checkout.ALREADY_PAID)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(len(self.provider.calls), calls)

    def test_money_that_needs_attention_blocks_a_second_charge(self):
        request = self.request()
        first = self.sent(request)
        payment = self.opened(first)
        self.provider.pay(payment.session_id, amount_total=1)  # the wrong amount arrived
        self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)
        self.refused(first)
        second = self.sent(request, miles="20")  # the office may still send a corrected price
        self.refused(second)  # but it can't be paid on top of the money already held
        payment.refresh_from_db()
        self.assertTrue(checkout.record_refund(payment.payment_intent_id, 1))  # refunded in Stripe
        self.assertEqual(self.press(second).status_code, 303)

    def test_when_stripe_is_down_nothing_is_half_made_and_the_next_press_finishes_the_same_attempt(self):
        quote = self.sent()
        self.provider.down = True
        response = self.press(quote)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, pay_views.UNAVAILABLE, status_code=503)
        self.assertEqual(response["Cache-Control"], "no-store")
        payment = Payment.objects.get()
        self.assertEqual((payment.attempt, payment.status, payment.session_id, payment.session_url),
                         (1, PaymentStatus.OPEN, None, ""))
        self.assertEqual(self.provider.sessions, {})
        # Opening the page still works, and still asks Stripe nothing.
        self.assertContains(self.client.get(self.pay_path(quote)), "Continue to secure payment")

        self.provider.down = False
        again = self.press(quote)
        self.assertEqual(again.status_code, 303)
        payment.refresh_from_db()
        self.assertEqual((Payment.objects.count(), payment.attempt), (1, 1))
        self.assertEqual(again["Location"], payment.session_url)
        first, second = self.created()
        # The same number and the same closing time: Stripe treats it as the same request.
        self.assertEqual((first["idempotency_key"], first["expires_at"]),
                         (second["idempotency_key"], second["expires_at"]))
        self.assertEqual(len(self.provider.sessions), 1)

    def test_an_attempt_stripe_refused_makes_way_for_a_new_one(self):
        quote = self.sent()
        refusal = payments.PaymentError("Stripe did not accept the request (HTTP 400, parameter_invalid).",
                                        step="create_session", http_status=400, code="parameter_invalid")
        with mock.patch.object(self.provider, "create_session", side_effect=refusal):
            self.assertEqual(self.press(quote).status_code, 503)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.FAILED)
        self.assertEqual(self.press(quote).status_code, 303)
        self.assertEqual([p.attempt for p in Payment.objects.order_by("attempt")], [1, 2])
        self.assertEqual(self.created()[-1]["idempotency_key"], f"{quote.pk}-2")

    def test_an_attempt_too_old_to_finish_makes_way_for_a_new_one(self):
        quote = self.sent()
        self.provider.down = True
        self.press(quote)
        self.provider.down = False
        # 31 minutes later Stripe would refuse the stored closing time (it wants 30 minutes at least).
        Payment.objects.update(expires_at=timezone.now().replace(microsecond=0) + timedelta(minutes=29))
        self.assertEqual(self.press(quote).status_code, 303)
        old, new = Payment.objects.order_by("attempt")
        self.assertEqual((old.status, old.session_id), (PaymentStatus.EXPIRED, None))
        self.assertEqual((new.attempt, new.status), (2, PaymentStatus.OPEN))
        self.assertEqual(self.created()[-1]["idempotency_key"], f"{quote.pk}-2")

    def test_once_a_page_has_closed_at_stripe_the_next_press_opens_a_new_one(self):
        quote = self.sent()
        first = self.opened(quote)
        past = timezone.now() - timedelta(minutes=1)
        Payment.objects.filter(pk=first.pk).update(expires_at=past)
        self.provider.set(first.session_id, expires_at=past)
        response = self.press(quote)
        self.assertEqual(response.status_code, 303)
        first.refresh_from_db()
        second = quote.payments.get(attempt=2)
        self.assertEqual((first.status, second.status), (PaymentStatus.EXPIRED, PaymentStatus.OPEN))
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(response["Location"], second.session_url)
        # Never two open at once.
        self.assertEqual(quote.payments.filter(status=PaymentStatus.OPEN).count(), 1)

    def test_the_customer_is_only_ever_sent_to_stripes_own_page(self):
        quote = self.sent()
        payment = self.opened(quote)
        Payment.objects.filter(pk=payment.pk).update(session_url="https://checkout.stripe.com.example.test/pay")
        with self.assertLogs("billing.checkout", "WARNING") as logs:
            response = self.press(quote)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Location", response)
        self.assertEqual(logs.output,
                         ["WARNING:billing.checkout:Payment start refused: step=redirect error=NotStripesPage"])

    def test_a_request_cancelled_while_stripe_is_answering_is_not_sent_to_pay(self):
        quote = self.sent()

        class CancelledMeanwhile(payments.FakeProvider):
            def create_session(inner, **kwargs):
                session = super().create_session(**kwargs)
                request = TripRequest.objects.get(pk=quote.trip_request_id)
                request.status = TripRequestStatus.CANCELLED
                request.save()
                checkout.expire_open(request)  # what cancelling does: the row has no session to close yet
                return session

        provider = CancelledMeanwhile()
        payments.substitute(provider)
        response = self.press(quote)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, checkout.CANCELLED)
        payment = Payment.objects.get()
        # The page Stripe opened is on record and closed, here and at Stripe.
        self.assertEqual(payment.status, PaymentStatus.EXPIRED)
        self.assertTrue(payment.session_id)
        self.assertEqual(provider.sessions[payment.session_id]["status"], "expired")

    def test_a_fault_shows_a_plain_page_and_logs_only_its_kind(self):
        quote = self.sent()
        boom = RuntimeError("jamie@example.com 1 Main St sk_test_notakey")
        with mock.patch.object(checkout, "start", side_effect=boom), \
                self.assertLogs("billing.pay_views", "ERROR") as logs:
            response = self.press(quote)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, pay_views.UNAVAILABLE, status_code=503)
        self.assertNotContains(response, "RuntimeError", status_code=503)
        self.assertEqual(logs.output, ["ERROR:billing.pay_views:Pay page failed: step=post error=RuntimeError"])


# --- Recording what Stripe says -----------------------------------------------------


class ApplySessionTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on(refund_policy="Our own refund rule.", what_paying_means="What paying means, in our words.")

    def test_a_paid_session_marks_the_price_paid_and_tells_the_office_and_the_customer(self):
        quote = self.sent()
        payment = self.opened(quote)
        reference = payments.reference(quote.trip_request_id)
        self.assertEqual(self.apply(payment), PaymentStatus.OPEN)  # not paid yet: nothing happens
        self.assertFalse(payments.quote_paid(quote))
        emails = len(mail.outbox)

        self.provider.pay(payment.session_id)
        self.assertEqual(self.apply(payment), PaymentStatus.PAID)
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.paid_cents, payment.payment_intent_id, payment.livemode),
                         (PaymentStatus.PAID, 6480, "pi_fake0001", False))
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(payment.attention_reason, "")
        self.assertTrue(payments.quote_paid(quote))
        self.assertTrue(payments.request_paid(quote.trip_request))
        # Paying accepts the price; it does not schedule the ride.
        self.assertEqual(TripRequest.objects.get(pk=quote.trip_request_id).status, TripRequestStatus.CONTACTED)
        self.assertFalse(Trip.objects.exists())

        entry = AuditLog.objects.get(action="payment_paid")
        self.assertEqual((entry.source, entry.actor, entry.resource_type, entry.resource_id),
                         ("stripe", None, "Payment", str(payment.pk)))
        self.assertEqual(entry.after, {
            "quote": str(quote.pk), "request": str(quote.trip_request_id), "session": payment.session_id,
            "payment_intent": "pi_fake0001", "asked_cents": 6480, "paid_cents": 6480,
        })

        office, customer = mail.outbox[emails:]
        self.assertEqual(office.to, [settings.STAFF_NOTIFICATION_EMAIL])
        self.assertEqual(office.subject, f"Price {reference} has been paid: schedule the ride")
        for body in (office.body, office.alternatives[0][0]):
            self.assertIn(f"/admin/transportation/triprequest/{quote.trip_request_id}/change/", body)
            self.assertIn(reference, body)
            self.assertNotIn("$", body)  # no amount in the shared inbox
            self.assertNotIn("64.80", body)
            self.assertNotIn("jamie@example.com", body)
            self.assertPrivate(body.replace(str(quote.trip_request_id), ""))
        self.assertEqual(customer.to, ["jamie@example.com"])
        self.assertIn("We received your payment", customer.subject)
        self.assertIn(reference, customer.subject)
        self.assertNotIn("64.80", customer.subject)
        for body in (customer.body, customer.alternatives[0][0]):
            self.assertIn(reference, body)
            self.assertIn("$64.80", body)
            self.assertIn("What paying means, in our words.", body)
            self.assertIn("Our own refund rule.", body)
            self.assertNotIn("/admin/", body)
            self.assertPrivate(body)
        payment.refresh_from_db()
        self.assertIsNotNone(payment.office_notified_at)
        self.assertIsNotNone(payment.customer_notified_at)
        for kind in (NotificationType.PAYMENT_RECEIVED_STAFF, NotificationType.PAYMENT_RECEIVED_CUSTOMER):
            self.assertEqual(NotificationLog.objects.filter(notification_type=kind, status="sent").count(), 1)

    def test_the_same_news_twice_changes_nothing_the_second_time(self):
        """The return page and the webhook race, and Stripe repeats itself: one record, one email each."""
        payment = self.opened()
        self.provider.pay(payment.session_id)
        emails = len(mail.outbox)
        self.assertEqual(self.apply(payment), PaymentStatus.PAID)
        paid_at = Payment.objects.get(pk=payment.pk).paid_at
        for _ in range(3):
            self.assertEqual(self.apply(payment), PaymentStatus.PAID)
        self.assertEqual(AuditLog.objects.filter(action="payment_paid").count(), 1)
        self.assertEqual(len(mail.outbox), emails + 2)
        self.assertEqual(Payment.objects.get(pk=payment.pk).paid_at, paid_at)

    def test_two_deliveries_at_the_same_moment_only_one_marks_it_paid(self):
        """The second delivery read the row as open before the first one saved "paid"."""
        payment = self.opened()
        self.provider.pay(payment.session_id)
        state = self.provider.retrieve_session(payment.session_id)
        stale = Payment.objects.select_related("quote").get(pk=payment.pk)
        request = stale.quote.trip_request
        self.assertEqual(self.apply(payment), PaymentStatus.PAID)
        self.assertEqual(stale.status, PaymentStatus.OPEN)
        status = checkout._apply(stale, request, state, failed=False, actor=None, recheck=False)
        self.assertEqual(status, PaymentStatus.PAID)
        self.assertEqual(AuditLog.objects.filter(action="payment_paid").count(), 1)

    def needs_attention(self, payment, expected: str):
        """Real money that can't be accepted: flagged with its reason, audited, and the office told."""
        emails = len(mail.outbox)
        self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)
        payment.refresh_from_db()
        reference = payments.reference(payment.quote.trip_request_id)
        self.assertEqual(payment.status, PaymentStatus.NEEDS_ATTENTION)
        self.assertIn(expected, payment.attention_reason)
        self.assertEqual(payment.payment_intent_id, "pi_fake" + payment.session_id[-4:])
        self.assertIsNotNone(payment.paid_at)
        entry = AuditLog.objects.get(action="payment_needs_attention", resource_id=str(payment.pk))
        self.assertEqual(entry.source, "stripe")
        self.assertTrue(any(expected in why for why in entry.after["why"]))
        (office,) = mail.outbox[emails:]  # the office only: the customer hears from a person
        self.assertEqual(office.to, [settings.STAFF_NOTIFICATION_EMAIL])
        self.assertEqual(office.subject, f"A payment for {reference} needs attention")
        for body in (office.body, office.alternatives[0][0]):
            self.assertIn(f"/admin/billing/payment/{payment.pk}/change/", body)
            self.assertNotIn("$", body)
            self.assertPrivate(body)
        # A repeat changes nothing and sends nothing.
        self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)
        self.assertEqual(len(mail.outbox), emails + 1)
        self.assertEqual(AuditLog.objects.filter(action="payment_needs_attention").count(), 1)
        self.assertFalse(AuditLog.objects.filter(action="payment_paid", resource_id=str(payment.pk)).exists())

    def test_a_paid_session_with_the_wrong_amount_needs_attention(self):
        payment = self.opened()
        self.provider.pay(payment.session_id, amount_total=100)
        self.needs_attention(payment, "Stripe took $1.00 but the price was $64.80.")
        self.assertEqual(payment.paid_cents, 100)
        # The money is on record, but the price does not count as paid.
        self.assertFalse(payments.quote_paid(payment.quote))
        self.assertFalse(payments.request_paid(payment.quote.trip_request))

    def test_a_paid_session_in_another_currency_mode_or_kind_needs_attention(self):
        for change, expected in (
            ({"currency": "eur"}, "not in US dollars"),
            ({"livemode": True}, "test payment where a real one was expected"),
            ({"mode": "subscription"}, "other than a one-time payment"),
            ({"metadata_quote": "0b0e7b1c-58a4-4e0c-9d52-3f8a6c1d2e4f"}, "names a different price"),
            ({"amount_total": None}, "an unknown amount"),
        ):
            with self.subTest(change=change):
                payment = self.opened(self.sent(self.request()))
                self.provider.pay(payment.session_id, **change)
                emails = len(mail.outbox)
                self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)
                payment.refresh_from_db()
                self.assertIn(expected, payment.attention_reason)
                self.assertEqual(len(mail.outbox), emails + 1)

    def test_a_payment_for_a_replaced_price_needs_attention(self):
        request = self.request()
        payment = self.opened(self.sent(request))
        self.sent(request, miles="20")
        self.provider.pay(payment.session_id)  # the customer still had the old page open
        self.needs_attention(payment, "replaced by a newer one")

    def test_a_payment_for_a_cancelled_or_declined_request_needs_attention(self):
        for status in (TripRequestStatus.CANCELLED, TripRequestStatus.DECLINED):
            with self.subTest(status=status):
                payment = self.opened(self.sent(self.request()))
                TripRequest.objects.filter(pk=payment.quote.trip_request_id).update(status=status)
                self.provider.pay(payment.session_id)
                self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)
                payment.refresh_from_db()
                self.assertIn(f"The request was {status} before the payment arrived.", payment.attention_reason)

    def test_a_payment_for_a_rider_who_became_a_medicaid_rider_needs_attention(self):
        payment = self.opened()
        request = payment.quote.trip_request
        request.passenger = make_passenger(payer=Payer.objects.create(name="TEST broker", payer_type="broker"))
        request.save()
        self.provider.pay(payment.session_id)
        self.needs_attention(payment, "Medicaid or broker payer")

    def test_two_payments_for_one_ride_the_second_needs_attention(self):
        quote = self.sent()
        first = self.opened(quote)
        second = self.make_payment(quote)  # a second page that should never have existed
        self.assertEqual((first.attempt, second.attempt), (1, 2))
        self.provider.pay(first.session_id)
        self.provider.pay(second.session_id)
        self.assertEqual(self.apply(first), PaymentStatus.PAID)
        second.refresh_from_db()
        self.assertEqual(second.status, PaymentStatus.EXPIRED)  # paying one closes every other page
        self.needs_attention(second, "already paid online: this is a second payment")
        first.refresh_from_db()
        self.assertEqual(first.status, PaymentStatus.PAID)
        self.assertEqual(Payment.objects.filter(status=PaymentStatus.PAID).count(), 1)

    def test_a_page_we_closed_that_was_paid_all_the_same_is_still_recorded(self):
        """Closed here but Stripe never heard: if everything still fits, the money is simply accepted."""
        payment = self.opened()
        Payment.objects.filter(pk=payment.pk).update(status=PaymentStatus.EXPIRED)
        self.provider.pay(payment.session_id)
        self.assertEqual(self.apply(payment), PaymentStatus.PAID)

    def test_an_expired_or_failed_session_only_changes_the_status(self):
        expired = self.opened()
        failed = self.opened(self.sent(self.request()))
        self.provider.set(expired.session_id, status="expired")
        self.provider.set(failed.session_id, status="complete", payment_status="unpaid")
        audit, emails = AuditLog.objects.count(), len(mail.outbox)
        self.assertEqual(self.apply(expired), PaymentStatus.EXPIRED)
        self.assertEqual(self.apply(failed), PaymentStatus.OPEN)  # complete and unpaid alone is "still processing"
        self.assertEqual(self.apply(failed, failed=True), PaymentStatus.FAILED)
        # No audit row and no email: nobody paid anything.
        self.assertEqual((AuditLog.objects.count(), len(mail.outbox)), (audit, emails))
        # A paid one is never un-paid by a later answer.
        paid = self.paid(self.sent(self.request()))
        self.provider.set(paid.session_id, status="expired", payment_status="unpaid")
        self.assertEqual(self.apply(paid), PaymentStatus.PAID)

    def test_a_session_this_system_did_not_open_is_ignored(self):
        other = self.provider.create_session(
            amount_cents=6480, reference="#3FA9C2D1", quote_id="0b0e7b1c-58a4-4e0c-9d52-3f8a6c1d2e4f",
            idempotency_key="someone-else-1", success_url=payments.success_url(),
            cancel_url=payments.cancel_url(), expires_at=payments.session_expiry(),
        )
        self.provider.pay(other.id)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(checkout.apply_session(self.provider.retrieve_session(other.id)), checkout.UNKNOWN)
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_an_email_that_failed_is_sent_by_the_next_delivery_and_only_that_one(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        emails = len(mail.outbox)
        with mock.patch.object(NotificationService, "send_payment_received", return_value=False):
            self.assertEqual(self.apply(payment), PaymentStatus.PAID)
        payment.refresh_from_db()
        self.assertIsNotNone(payment.office_notified_at)
        self.assertIsNone(payment.customer_notified_at)
        self.assertEqual(len(mail.outbox), emails + 1)
        self.assertEqual(self.apply(payment), PaymentStatus.PAID)  # Stripe's repeat, or the daily check
        (customer,) = mail.outbox[emails + 1:]
        self.assertEqual(customer.to, ["jamie@example.com"])
        self.assertIsNotNone(Payment.objects.get(pk=payment.pk).customer_notified_at)
        # An email provider that raises never undoes or fails the payment either.
        other = self.opened(self.sent(self.request(email="other@example.com")))
        self.provider.pay(other.session_id)
        with mock.patch.object(NotificationService, "notify_payment_received", side_effect=RuntimeError("boom")), \
                self.assertLogs("billing.checkout", "ERROR") as logs:
            self.assertEqual(self.apply(other), PaymentStatus.PAID)
        self.assertEqual(logs.output,
                         ["ERROR:billing.checkout:Payment email failed: stamp=office_notified_at error=RuntimeError"])
        other.refresh_from_db()
        self.assertEqual(other.status, PaymentStatus.PAID)
        self.assertIsNone(other.office_notified_at)


# --- The webhook -----------------------------------------------------------------


class WebhookTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on()

    def test_without_a_secret_the_webhook_is_not_there(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        body = self.session_event(payment)
        with self.settings(STRIPE_WEBHOOK_SECRET=""):
            response = self.deliver(body, signature(body, secret=""))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)
        self.assertFalse(PaymentEvent.objects.exists())

    def test_only_post_and_no_csrf_token_needed(self):
        self.assertEqual(self.client.get(WEBHOOK).status_code, 405)
        payment = self.opened()
        self.provider.pay(payment.session_id)
        response = self.deliver(self.session_event(payment), client=Client(enforce_csrf_checks=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)

    def test_a_signed_report_of_a_paid_session_marks_it_paid_once(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        emails = len(mail.outbox)
        body = self.session_event(payment)
        retrieved = self.asked("retrieve_session")
        response = self.deliver(body)
        self.assertEqual((response.status_code, response.json()), (200, {"detail": "Recorded."}))
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)
        self.assertEqual(self.asked("retrieve_session"), retrieved + 1)  # Stripe was asked: the report is a hint
        event = PaymentEvent.objects.get()
        self.assertEqual((event.event_id, event.event_type), ("evt_test_0001", "checkout.session.completed"))
        self.assertEqual(len(mail.outbox), emails + 2)

        # The same event again (Stripe repeats itself, or someone replays it): 200 and nothing happens.
        again = self.deliver(body)
        self.assertEqual((again.status_code, again.json()), (200, {"detail": "Already handled."}))
        self.assertEqual(self.asked("retrieve_session"), retrieved + 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="payment_paid").count(), 1)
        self.assertEqual(len(mail.outbox), emails + 2)
        # A new event about the same paid session changes nothing either.
        third = self.deliver(self.session_event(payment, event_id="evt_test_0002"))
        self.assertEqual(third.status_code, 200)
        self.assertEqual(AuditLog.objects.filter(action="payment_paid").count(), 1)
        self.assertEqual(len(mail.outbox), emails + 2)

    def test_a_forged_or_stale_signature_is_refused_before_anything_is_read(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        body = self.session_event(payment)
        calls = len(self.provider.calls)
        stale = signature(body, t=int(time.time()) - 301)
        future = signature(body, t=int(time.time()) + 301)
        for header in ("", "t=1,v1=00", signature(body, secret="whsec_test_someoneelse"), stale, future,
                       signature(body, scheme="v0"), signature(body + b" ")):
            with self.assertLogs("billing.pay_views", "WARNING") as logs:
                response = self.deliver(body, header)
            self.assertEqual(response.status_code, 400, header)
            # The log says what was refused and nothing of what was sent.
            self.assertEqual(logs.output, ["WARNING:billing.pay_views:Stripe webhook refused: step=signature"])
        with self.assertLogs("billing.pay_views", "WARNING"):
            self.assertEqual(self.client.post(WEBHOOK, data=body, content_type="application/json").status_code, 400)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)
        self.assertFalse(PaymentEvent.objects.exists())
        self.assertEqual(len(self.provider.calls), calls)  # Stripe was never asked

    def test_while_a_secret_is_rolled_either_one_signs(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        body = self.session_event(payment)
        with self.settings(STRIPE_WEBHOOK_SECRET=f"whsec_test_thenewone, {HOOK}"):
            self.assertEqual(self.deliver(body, signature(body, secret="whsec_test_thenewone")).status_code, 200)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)

    def test_the_webhooks_word_alone_never_marks_a_price_paid(self):
        """A correctly signed report that says "paid" while Stripe itself says the session is still open."""
        payment = self.opened()
        body = self.event("checkout.session.completed", {
            "id": payment.session_id, "object": "checkout.session", "status": "complete", "payment_status": "paid",
            "amount_total": 6480, "currency": "usd", "payment_intent": "pi_forged", "mode": "payment",
            "metadata": {"quote": str(payment.quote_id)},
        })
        self.assertEqual(self.deliver(body).status_code, 200)
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.payment_intent_id, payment.paid_cents),
                         (PaymentStatus.OPEN, None, None))
        self.assertFalse(AuditLog.objects.filter(action="payment_paid").exists())

    def test_a_signed_body_that_is_not_an_event_is_refused(self):
        for body in (b"\xff\xfe not text", b"not json", b"[]", b'"text"', b"{}", b'{"id": 5, "type": "x"}',
                     b'{"id": "evt_test_1"}', b'{"id": "../../x", "type": "checkout.session.completed"}',
                     b'{"id": "evt_test_1", "type": "Not A Type"}'):
            self.assertEqual(self.deliver(body).status_code, 400, body)
        self.assertFalse(PaymentEvent.objects.exists())
        # An event with no object at all is recorded and ignored.
        odd = b'{"id": "evt_test_odd", "type": "checkout.session.completed", "livemode": false, "data": null}'
        self.assertEqual(self.deliver(odd).json(), {"detail": "Ignored."})

    def test_sessions_and_charges_this_system_did_not_make_are_ignored_with_200(self):
        calls = len(self.provider.calls)
        for number, (kind, thing) in enumerate((
            ("checkout.session.completed", {"id": "cs_test_someoneelses"}),
            ("checkout.session.completed", {"id": "cs_test/../../v1/charges"}),
            ("checkout.session.expired", {"id": None}),
            ("charge.refunded", {"payment_intent": "pi_someoneelses", "amount_refunded": 500}),
            ("charge.dispute.created", {"payment_intent": "pi_someoneelses"}),
            ("charge.refunded", {"payment_intent": None, "amount_refunded": 500}),
            ("invoice.paid", {"id": "in_test_1"}),
        )):
            response = self.deliver(self.event(kind, thing, event_id=f"evt_test_{number}"))
            self.assertEqual(response.status_code, 200, kind)
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(PaymentEvent.objects.count(), 7)  # recorded, so a repeat is a no-op
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_a_report_in_the_wrong_mode_is_never_applied(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        with self.assertLogs("billing.pay_views", "WARNING") as logs:
            response = self.deliver(self.session_event(payment, livemode=True))  # a "live" report on a test server
        self.assertEqual(response.json(), {"detail": "Ignored."})
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)
        self.assertEqual(logs.output, ["WARNING:billing.pay_views:Stripe webhook ignored: step=livemode "
                                       "type=checkout.session.completed"])

    def test_when_stripe_can_not_be_asked_the_answer_is_503_and_the_retry_works(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        body = self.session_event(payment)
        self.provider.down = True
        self.assertEqual(self.deliver(body).status_code, 503)
        self.assertFalse(PaymentEvent.objects.exists())  # not remembered: Stripe sends it again
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)
        self.provider.down = False
        self.assertEqual(self.deliver(body).status_code, 200)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)

    def test_a_session_stripe_no_longer_knows_is_ignored_not_retried_for_ever(self):
        payment = self.opened()
        del self.provider.sessions[payment.session_id]
        self.assertEqual(self.deliver(self.session_event(payment)).json(), {"detail": "Ignored."})
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_expired_and_failed_reports_update_the_status(self):
        expired = self.opened()
        self.provider.set(expired.session_id, status="expired")
        self.assertEqual(self.deliver(self.session_event(expired, "checkout.session.expired")).status_code, 200)
        self.assertEqual(Payment.objects.get(pk=expired.pk).status, PaymentStatus.EXPIRED)
        failed = self.opened(self.sent(self.request(email="other@example.com")))
        self.provider.set(failed.session_id, status="complete", payment_status="unpaid")
        self.deliver(self.session_event(failed, "checkout.session.async_payment_failed", event_id="evt_test_0002"))
        self.assertEqual(Payment.objects.get(pk=failed.pk).status, PaymentStatus.FAILED)
        late = self.opened(self.sent(self.request(email="third@example.com")))
        self.provider.pay(late.session_id)
        self.deliver(self.session_event(late, "checkout.session.async_payment_succeeded", event_id="evt_test_0003"))
        self.assertEqual(Payment.objects.get(pk=late.pk).status, PaymentStatus.PAID)

    def test_a_refund_made_in_stripe_is_recorded_and_the_office_told_without_the_amount(self):
        payment = self.paid()
        reference = payments.reference(payment.quote.trip_request_id)
        emails = len(mail.outbox)
        charge = {"id": "ch_test_1", "object": "charge", "payment_intent": payment.payment_intent_id,
                  "amount_refunded": 1000}
        self.assertEqual(self.deliver(self.event("charge.refunded", charge, event_id="evt_test_r1")).status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.refunded_cents, 1000)
        self.assertIsNotNone(payment.refunded_at)
        self.assertEqual(payment.status, PaymentStatus.PAID)
        self.assertTrue(payments.request_paid(payment.quote.trip_request))  # a part refund: still paid
        entry = AuditLog.objects.get(action="payment_refunded")
        self.assertEqual((entry.source, entry.resource_id), ("stripe", str(payment.pk)))
        self.assertEqual(entry.after, {"payment_intent": payment.payment_intent_id, "refunded_cents": 1000,
                                       "paid_cents": 6480})
        (office,) = mail.outbox[emails:]
        self.assertEqual(office.to, [settings.STAFF_NOTIFICATION_EMAIL])
        self.assertEqual(office.subject, f"A refund was recorded for {reference}")
        self.assertNotIn("$", self.bodies(office))
        self.assertNotIn("10.00", self.bodies(office))
        self.assertPrivate(self.bodies(office))

        # The rest is refunded: the running total goes up, never past what was paid, never back down.
        for number, total in enumerate((6480, 99999, 500)):
            charge["amount_refunded"] = total
            self.deliver(self.event("charge.refunded", charge, event_id=f"evt_test_more{number}"))
            self.assertEqual(Payment.objects.get(pk=payment.pk).refunded_cents, 6480)
        self.assertEqual(AuditLog.objects.filter(action="payment_refunded").count(), 2)
        self.assertEqual(len(mail.outbox), emails + 2)
        self.assertFalse(payments.request_paid(payment.quote.trip_request))  # refunded in full
        for bad in (True, "6480", -5, 0, None, 12.5):
            self.assertFalse(checkout.record_refund(payment.payment_intent_id, bad))

    def test_a_dispute_is_recorded_once_and_the_office_told(self):
        payment = self.paid()
        emails = len(mail.outbox)
        dispute = {"id": "dp_test_1", "object": "dispute", "payment_intent": payment.payment_intent_id}
        self.deliver(self.event("charge.dispute.created", dispute, event_id="evt_test_d1"))
        payment.refresh_from_db()
        first = payment.disputed_at
        self.assertIsNotNone(first)
        self.deliver(self.event("charge.dispute.created", dispute, event_id="evt_test_d2"))
        self.assertEqual(Payment.objects.get(pk=payment.pk).disputed_at, first)
        self.assertEqual(AuditLog.objects.filter(action="payment_disputed", source="stripe").count(), 1)
        (office,) = mail.outbox[emails:]
        self.assertEqual(office.subject,
                         f"The payment for {payments.reference(payment.quote.trip_request_id)} was disputed")
        self.assertNotIn("$", self.bodies(office))

    def test_an_email_fault_never_turns_into_a_refusal(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        with mock.patch.object(NotificationService, "_send", side_effect=RuntimeError("boom")), \
                self.assertLogs("billing.checkout", "ERROR"):
            response = self.deliver(self.session_event(payment))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)


# --- The page Stripe sends the payer back to ------------------------------------------


class ReturnPageTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on()

    def back(self, session_id):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.get("/pay/return/", {"session_id": session_id})

    def test_a_paid_session_is_recorded_and_the_payer_sent_to_the_received_page(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        emails = len(mail.outbox)
        response = self.back(payment.session_id)
        self.assertRedirects(response, f"{settings.WEBSITE_URL}/payment/received", fetch_redirect_response=False)
        for name, value in HEADERS.items():
            self.assertEqual(response[name], value)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)
        self.assertEqual(len(mail.outbox), emails + 2)
        # The webhook arrives afterwards: nothing more happens.
        self.assertEqual(self.deliver(self.session_event(payment)).status_code, 200)
        self.assertEqual(len(mail.outbox), emails + 2)
        self.assertEqual(AuditLog.objects.filter(action="payment_paid").count(), 1)
        # Reloading the return page is harmless too.
        self.assertRedirects(self.back(payment.session_id), f"{settings.WEBSITE_URL}/payment/received",
                             fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), emails + 2)

    def test_the_address_alone_proves_nothing(self):
        """Someone types the return address of a session that was never paid."""
        payment = self.opened()
        response = self.back(payment.session_id)
        self.assertRedirects(response, f"{settings.WEBSITE_URL}/payment/cancelled", fetch_redirect_response=False)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)

    def test_an_unknown_or_malformed_session_goes_to_cancelled_without_asking_stripe(self):
        calls = len(self.provider.calls)
        for session_id in ("cs_test_someoneelses", "", "{CHECKOUT_SESSION_ID}", "cs_test/../../v1/customers",
                           "x" * 500):
            response = self.back(session_id)
            self.assertRedirects(response, f"{settings.WEBSITE_URL}/payment/cancelled",
                                 fetch_redirect_response=False)
            self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/pay/return/").status_code, 302)
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(self.client.post("/pay/return/").status_code, 405)

    def test_money_that_needs_attention_is_still_money_received(self):
        payment = self.opened()
        self.provider.pay(payment.session_id, amount_total=1)
        response = self.back(payment.session_id)
        # Never "no payment was taken" when one was.
        self.assertRedirects(response, f"{settings.WEBSITE_URL}/payment/received", fetch_redirect_response=False)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.NEEDS_ATTENTION)

    def test_when_stripe_can_not_be_asked_the_payer_is_told_to_expect_an_email(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        self.provider.down = True
        response = self.back(payment.session_id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We are confirming your payment")
        self.assertNotContains(response, "PaymentError")
        self.assertNotContains(response, payment.session_id)
        for name, value in HEADERS.items():
            self.assertEqual(response[name], value)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)  # the daily check records it


# --- Cancelling and declining -------------------------------------------------------


class RiderAccount:
    """Stands in for the rider's or the facility user's account: `cancel_request` only reads its user."""

    def __init__(self, user):
        self.user = user


class CancelTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on()
        self.account = RiderAccount(User.objects.create_user(username="rider-test", password="testpass123"))

    def login(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")
        return user

    def test_cancelling_online_closes_the_open_payment_page(self):
        quote = self.sent()
        payment = self.opened(quote)
        emails = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            rider_services.cancel_request(self.account, quote.trip_request)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.EXPIRED)
        self.assertEqual(self.provider.sessions[payment.session_id]["status"], "expired")
        (alert,) = mail.outbox[emails:]
        self.assertNotIn("paid online", self.bodies(alert))
        self.assertContains(self.press(quote), checkout.CANCELLED)

    def test_cancelling_a_paid_ride_refunds_nothing_and_tells_the_office_a_refund_may_be_due(self):
        quote = self.sent()
        payment = self.paid(quote)
        emails = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            rider_services.cancel_request(self.account, quote.trip_request, by="facility", source="facility_portal")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.refunded_cents), (PaymentStatus.PAID, 0))
        (alert,) = mail.outbox[emails:]
        self.assertIn("cancelled by the facility", alert.subject)
        for body in (alert.body, alert.alternatives[0][0]):
            self.assertIn("This ride was paid online: a refund may be due.", body)
            self.assertNotIn("$", body)
            self.assertNotIn("64.80", body)
        self.assertEqual(self.asked("expire_session"), 0)

    def test_cancelling_works_as_before_while_the_payment_tables_are_missing(self):
        quote = self.sent()
        with connection.cursor() as cursor:  # undone with the test's transaction
            cursor.execute("ALTER TABLE billing_payment RENAME TO billing_payment_gone")
        with self.assertLogs("billing.payments", "WARNING"), self.captureOnCommitCallbacks(execute=True):
            rider_services.cancel_request(self.account, quote.trip_request)
        self.assertEqual(TripRequest.objects.get(pk=quote.trip_request_id).status, TripRequestStatus.CANCELLED)
        self.assertIn("cancelled by the rider", mail.outbox[-1].subject)

    def set_status_in_admin(self, request, status):
        """The office changes the request's status on its admin page and saves."""
        url = f"/admin/transportation/triprequest/{request.pk}/change/"
        form = self.client.get(url).context["adminform"].form
        data = {name: ("" if value is None else value) for name, value in form.initial.items()}
        data.update({"status": status, "requested_datetime_0": "2027-01-13", "requested_datetime_1": "09:00:00",
                     "return_datetime_0": "", "return_datetime_1": "", "passenger": ""})
        with self.captureOnCommitCallbacks(execute=True):
            saved = self.client.post(url, data, follow=True)
        errors = saved.context["adminform"].form.errors if saved.context and "adminform" in saved.context else ""
        self.assertEqual(TripRequest.objects.get(pk=request.pk).status, status, errors)
        return saved

    def test_the_office_cancelling_or_declining_closes_the_open_payment_page(self):
        self.login("Administrator")
        for status in (TripRequestStatus.CANCELLED, TripRequestStatus.DECLINED):
            with self.subTest(status=status):
                quote = self.sent(self.request())
                payment = self.opened(quote)
                saved = self.set_status_in_admin(quote.trip_request, status)
                payment.refresh_from_db()
                self.assertEqual(payment.status, PaymentStatus.EXPIRED)
                self.assertEqual(self.provider.sessions[payment.session_id]["status"], "expired")
                self.assertNotContains(saved, "a refund may be due")

    def test_the_office_cancelling_a_paid_ride_is_told_a_refund_may_be_due(self):
        self.login("Administrator")
        quote = self.sent()
        payment = self.paid(quote)
        saved = self.set_status_in_admin(quote.trip_request, TripRequestStatus.CANCELLED)
        self.assertContains(saved, "This ride was paid online: a refund may be due.")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.refunded_cents), (PaymentStatus.PAID, 0))
        # Saving the page again without changing the status says nothing more.
        again = self.set_status_in_admin(quote.trip_request, TripRequestStatus.CANCELLED)
        self.assertNotContains(again, "a refund may be due")


# --- The daily check ---------------------------------------------------------------


class DailyCheckTests(FlowTestCase):
    def test_while_stripe_is_not_set_up_nothing_is_asked(self):
        self.assertEqual(checkout.daily_check(), "off")
        self.assertEqual(self.provider.calls, [])
        with self.settings(CRON_SECRET="s3cret"):
            response = self.client.get("/api/v1/cron/daily/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payment_checks"], "off")
        self.assertEqual(self.provider.calls, [])

    def test_a_payment_whose_report_never_arrived_is_recorded_by_the_daily_job(self):
        self.payments_on()
        paid = self.opened()
        still_open = self.opened(self.sent(self.request(email="other@example.com")))
        self.provider.pay(paid.session_id)
        emails = len(mail.outbox)
        with self.settings(CRON_SECRET="s3cret"), self.captureOnCommitCallbacks(execute=True):
            response = self.client.get("/api/v1/cron/daily/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.json()["payment_checks"],
                         {"checked": 2, "paid": 1, "needs_attention": 0, "unreachable": 0})
        self.assertEqual(Payment.objects.get(pk=paid.pk).status, PaymentStatus.PAID)
        self.assertEqual(Payment.objects.get(pk=still_open.pk).status, PaymentStatus.OPEN)
        self.assertEqual(len(mail.outbox), emails + 2)
        # The next day's run asks only about what is still open.
        asked = self.asked("retrieve_session")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(checkout.daily_check()["checked"], 1)
        self.assertEqual(self.asked("retrieve_session"), asked + 1)
        self.assertEqual(len(mail.outbox), emails + 2)

    def test_only_the_last_three_days_and_pages_that_closed_are_marked_closed(self):
        self.payments_on()
        old = self.opened()
        Payment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3, minutes=1))
        closed = self.opened(self.sent(self.request(email="other@example.com")))
        self.provider.set(closed.session_id, status="expired")
        never_opened = self.sent(self.request(email="third@example.com"))
        self.provider.down = True
        self.press(never_opened)
        self.provider.down = False
        Payment.objects.filter(quote=never_opened).update(expires_at=timezone.now() - timedelta(minutes=1))
        calls = self.asked("retrieve_session")
        self.assertEqual(checkout.daily_check(), {"checked": 1, "paid": 0, "needs_attention": 0, "unreachable": 0})
        self.assertEqual(self.asked("retrieve_session"), calls + 1)
        self.assertEqual(Payment.objects.get(pk=old.pk).status, PaymentStatus.OPEN)
        self.assertEqual(Payment.objects.get(pk=closed.pk).status, PaymentStatus.EXPIRED)
        self.assertEqual(Payment.objects.get(quote=never_opened).status, PaymentStatus.EXPIRED)

    def test_a_page_closed_early_that_stripe_never_heard_about_is_checked_too(self):
        self.payments_on()
        quote = self.sent()
        payment = self.opened(quote)
        self.provider.down = True
        with self.captureOnCommitCallbacks(execute=True):
            rider_services.cancel_request(RiderAccount(User.objects.create_user(username="rider-test")),
                                          quote.trip_request)
        self.provider.down = False
        self.provider.pay(payment.session_id)  # paid on the page Stripe still showed; the report was lost
        emails = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            result = checkout.daily_check()
        self.assertEqual(result, {"checked": 1, "paid": 0, "needs_attention": 1, "unreachable": 0})
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.NEEDS_ATTENTION)
        self.assertIn("cancelled before the payment arrived", payment.attention_reason)
        self.assertEqual(len(mail.outbox), emails + 1)

    def test_stripe_being_down_leaves_everything_for_tomorrow(self):
        self.payments_on()
        for number in range(4):
            self.opened(self.sent(self.request(email=f"rider{number}@example.com")))
        self.provider.down = True
        calls = self.asked("retrieve_session")
        self.assertEqual(checkout.daily_check(), {"checked": 0, "paid": 0, "needs_attention": 0, "unreachable": 4})
        self.assertEqual(self.asked("retrieve_session"), calls + 2)  # two misses, then it stops asking
        self.assertEqual(Payment.objects.filter(status=PaymentStatus.OPEN).count(), 4)

    def test_an_email_still_owed_is_sent_by_the_daily_job(self):
        self.payments_on()
        payment = self.opened()
        self.provider.pay(payment.session_id)
        with mock.patch.object(NotificationService, "send_payment_received", return_value=False):
            self.apply(payment)
        emails = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            checkout.daily_check()
        (customer,) = mail.outbox[emails:]
        self.assertIn("We received your payment", customer.subject)
        checkout.daily_check()
        self.assertEqual(len(mail.outbox), emails + 1)

    def test_a_fault_in_the_payment_check_never_fails_the_daily_job(self):
        self.payments_on()
        with mock.patch.object(checkout, "daily_check", side_effect=RuntimeError("sk_test_notakey")), \
                self.settings(CRON_SECRET="s3cret"), self.assertLogs("config.cron", "ERROR") as logs:
            response = self.client.get("/api/v1/cron/daily/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payment_checks"], "failed")
        self.assertEqual(logs.output, ["ERROR:config.cron:Payment check failed: error=RuntimeError"])


# --- What the office sees -------------------------------------------------------------


class AdminPaymentTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on()

    def login(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")
        return user

    @staticmethod
    def page(payment) -> str:
        return f"/admin/billing/payment/{payment.pk}/change/"

    def check(self, payment):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f"/admin/billing/payment/{payment.pk}/check/", follow=True)

    def test_check_with_stripe_records_a_payment_whose_report_never_arrived(self):
        admin = self.login("Administrator")
        payment = self.opened()
        page = self.client.get(self.page(payment))
        self.assertContains(page, "Check with Stripe")
        self.assertContains(page, f"/admin/billing/payment/{payment.pk}/check/")

        nothing = self.check(payment)
        self.assertContains(nothing, "this payment has not been paid")
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)

        self.provider.pay(payment.session_id)
        emails = len(mail.outbox)
        done = self.check(payment)
        self.assertContains(done, "It is recorded as paid.")
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)
        self.assertEqual(len(mail.outbox), emails + 2)
        paid = AuditLog.objects.get(action="payment_paid")
        self.assertEqual((paid.actor, paid.source), (admin, "admin"))
        checked = AuditLog.objects.filter(action="payment_checked").order_by("created_at").last()
        self.assertEqual((checked.actor, checked.before, checked.after),
                         (admin, {"status": "open"}, {"status": "paid"}))
        # A settled payment has no button, and the address does nothing.
        self.assertNotContains(self.client.get(self.page(payment)), "Check with Stripe")
        calls = len(self.provider.calls)
        self.assertContains(self.check(payment), "There is nothing to check on this payment.")
        self.assertEqual(len(self.provider.calls), calls)

    def test_the_check_is_a_post_for_staff_who_may_see_payments(self):
        payment = self.opened()
        self.provider.pay(payment.session_id)
        self.login("Dispatcher")
        self.assertEqual(self.client.post(f"/admin/billing/payment/{payment.pk}/check/").status_code, 403)
        self.client.logout()
        self.login("Administrator")
        calls = len(self.provider.calls)
        self.assertEqual(self.client.get(f"/admin/billing/payment/{payment.pk}/check/").status_code, 405)
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)

    def test_when_stripe_can_not_be_asked_the_page_says_so_safely(self):
        self.login("Administrator")
        payment = self.opened()
        self.provider.down = True
        response = self.check(payment)
        self.assertContains(response, "Stripe could not be asked.")
        self.assertEqual(Payment.objects.get().status, PaymentStatus.OPEN)

    def test_a_payment_that_needed_attention_is_accepted_once_its_cause_is_fixed(self):
        self.login("Administrator")
        payment = self.opened()
        request = payment.quote.trip_request
        TripRequest.objects.filter(pk=request.pk).update(status=TripRequestStatus.CANCELLED)
        self.provider.pay(payment.session_id)
        self.assertEqual(self.apply(payment), PaymentStatus.NEEDS_ATTENTION)
        page = self.client.get(self.page(payment))
        self.assertContains(page, "Check with Stripe")
        self.assertContains(page, "The request was cancelled before the payment arrived.")

        still = self.check(payment)  # nothing was fixed yet
        self.assertContains(still, "still needs attention")
        self.assertEqual(Payment.objects.get().status, PaymentStatus.NEEDS_ATTENTION)

        TripRequest.objects.filter(pk=request.pk).update(status=TripRequestStatus.CONTACTED)  # cancelled by mistake
        emails = len(mail.outbox)
        self.assertContains(self.check(payment), "It is recorded as paid.")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.attention_reason), (PaymentStatus.PAID, ""))
        (customer,) = mail.outbox[emails:]  # the office already knows; the customer is told now
        self.assertIn("We received your payment", customer.subject)

    def test_the_request_page_and_the_prices_list_say_paid_online(self):
        self.login("Administrator")
        request = self.request()
        quote = self.sent(request)
        request_page = f"/admin/transportation/triprequest/{request.pk}/change/"
        self.assertNotContains(self.client.get(request_page), "Paid online on")
        self.assertNotContains(self.client.get("/admin/billing/quote/"), "Paid online on")
        payment = self.paid(quote)
        # Staff screens are in Texas time.
        texas = ZoneInfo(settings.OPERATIONS_TIME_ZONE)
        paid_on = f"Paid online on {timezone.localtime(payment.paid_at, texas):%b %d, %Y}"
        self.assertContains(self.client.get(request_page), paid_on)
        self.assertContains(self.client.get("/admin/billing/quote/"), paid_on)
        self.assertContains(self.client.get(f"/admin/billing/quote/{quote.pk}/change/"), paid_on)
        # Refunded in full: it no longer counts as paid.
        checkout.record_refund(payment.payment_intent_id, 6480)
        self.assertNotContains(self.client.get(request_page), "Paid online on")
        self.assertNotContains(self.client.get("/admin/billing/quote/"), "Paid online on")

    def test_the_price_page_lists_the_payments_and_refuses_a_new_price_after_payment(self):
        self.login("Administrator")
        request = self.request()
        quote = self.sent(request)
        url = f"/admin/billing/quote/send/{request.pk}/"
        self.assertNotContains(self.client.get(url), "data-quote-payments")
        payment = self.opened(quote)
        page = self.client.get(url)
        self.assertContains(page, "data-quote-payments")
        self.assertContains(page, "Open (not paid yet)")
        self.assertContains(page, f"/admin/billing/payment/{payment.pk}/change/")
        self.assertContains(page, "data-quote-price")  # a new price can still be sent
        self.assertNotContains(page, "data-quote-paid")

        self.provider.pay(payment.session_id)
        self.apply(payment)
        emails, quotes_before = len(mail.outbox), Quote.objects.count()
        page = self.client.get(url)
        self.assertContains(page, quotes.ALREADY_PAID)
        self.assertContains(page, "data-quote-paid")
        self.assertNotContains(page, "data-quote-price")  # no price, no Email button
        refused = self.client.post(url, {"miles": "20", "wait_minutes": "0", "checked_amount": "80.00",
                                         "miles_checked": "1", "customer_pays": "1", "send": "1"})
        self.assertContains(refused, quotes.ALREADY_PAID)
        self.assertEqual((len(mail.outbox), Quote.objects.count()), (emails, quotes_before))

    def test_the_admin_pages_work_while_the_payment_tables_are_missing(self):
        self.login("Administrator")
        request = self.request()
        quote = self.sent(request)
        with connection.cursor() as cursor:  # undone with the test's transaction
            cursor.execute("ALTER TABLE billing_payment RENAME TO billing_payment_gone")
            cursor.execute("ALTER TABLE billing_paymentterms RENAME TO billing_paymentterms_gone")
        with self.assertLogs("billing.payments", "WARNING"):
            for url in (f"/admin/transportation/triprequest/{request.pk}/change/", "/admin/billing/quote/",
                        f"/admin/billing/quote/{quote.pk}/change/", f"/admin/billing/quote/send/{request.pk}/",
                        "/admin/"):
                self.assertEqual(self.client.get(url).status_code, 200, url)
            # A price can still be emailed, as before online payment.
            second = self.sent(request, miles="20")
        self.assertTrue(second.delivered)
        self.assertNotIn("/pay/", self.bodies(mail.outbox[-1]))


class DashboardPaymentTests(FlowTestCase):
    def test_with_payment_off_and_nothing_to_list_the_dashboard_is_as_before(self):
        self.sent()
        for group in ("Administrator", "Dispatcher"):
            dash = build_dashboard(staff(group.lower(), group))
            self.assertIsNone(panel(dash, "Paid, waiting"))
            self.assertIsNone(panel(dash, "Payments needing attention"))

    def test_paid_rides_waiting_to_be_scheduled_are_listed_without_amounts(self):
        self.payments_on()
        dispatcher, admin = staff("dispatcher", "Dispatcher"), staff("administrator", "Administrator")
        empty = panel(build_dashboard(dispatcher), "Paid, waiting")
        self.assertEqual(empty.rows, [])
        waiting = self.paid(self.sent(self.request(full_name="Waiting Rider")))
        scheduled = self.paid(self.sent(self.request(full_name="Scheduled Rider", email="two@example.com")))
        Trip.objects.create(trip_request=scheduled.quote.trip_request, passenger=make_passenger(),
                            scheduled_pickup_datetime=timezone.now() + timedelta(days=2))
        self.opened(self.sent(self.request(full_name="Unpaid Rider", email="three@example.com")))
        for user in (dispatcher, admin):
            listed = panel(build_dashboard(user), "Paid, waiting to be scheduled")
            self.assertEqual([row.cells[0] for row in listed.rows], ["Waiting Rider"])
            self.assertEqual(listed.rows[0].url,
                             f"/admin/transportation/triprequest/{waiting.quote.trip_request_id}/change/")
            self.assertFalse(any("$" in cell or "64.80" in cell for row in listed.rows for cell in row.cells))
        # A refund in full takes it off the list.
        checkout.record_refund(waiting.payment_intent_id, 6480)
        self.assertEqual(panel(build_dashboard(dispatcher), "Paid, waiting").rows, [])

    def test_payments_needing_attention_are_listed_with_amounts_for_administrators_only(self):
        self.payments_on()
        dispatcher, admin = staff("dispatcher", "Dispatcher"), staff("administrator", "Administrator")
        self.assertEqual(panel(build_dashboard(admin), "Payments needing attention").rows, [])
        self.assertIsNone(panel(build_dashboard(dispatcher), "Payments needing attention"))

        wrong = self.opened(self.sent(self.request(full_name="Wrong Amount")))
        self.provider.pay(wrong.session_id, amount_total=100)
        self.apply(wrong)
        cancelled = self.paid(self.sent(self.request(full_name="Cancelled Rider")))
        TripRequest.objects.filter(pk=cancelled.quote.trip_request_id).update(status=TripRequestStatus.CANCELLED)
        disputed = self.paid(self.sent(self.request(full_name="Disputed Rider")))
        checkout.record_dispute(disputed.payment_intent_id)
        self.paid(self.sent(self.request(full_name="Fine Rider")))  # paid and fine: not listed

        listed = panel(build_dashboard(admin), "Payments needing attention")
        rows = {row.url: row.cells for row in listed.rows}
        self.assertEqual(set(rows), {f"/admin/billing/payment/{p.pk}/change/" for p in (wrong, cancelled, disputed)})
        self.assertEqual(rows[f"/admin/billing/payment/{wrong.pk}/change/"][:3],
                         [payments.reference(wrong.quote.trip_request_id),
                          "Stripe took $1.00 but the price was $64.80.", "$1.00"])
        self.assertIn("a refund may be due", rows[f"/admin/billing/payment/{cancelled.pk}/change/"][1])
        self.assertEqual(rows[f"/admin/billing/payment/{cancelled.pk}/change/"][2], "$64.80")
        self.assertIn("disputed", rows[f"/admin/billing/payment/{disputed.pk}/change/"][1])
        self.assertTrue(all(row.alert for row in listed.rows))
        self.assertIsNone(panel(build_dashboard(dispatcher), "Payments needing attention"))
        # The paid-and-cancelled ride is not also "waiting to be scheduled".
        # (nor is the one that needs attention).
        waiting = panel(build_dashboard(dispatcher), "Paid, waiting")
        self.assertEqual(sorted(row.cells[0] for row in waiting.rows), ["Disputed Rider", "Fine Rider"])

        # Refunded in full in Stripe: each drops off the list.
        checkout.record_refund(cancelled.payment_intent_id, 6480)
        wrong.refresh_from_db()
        checkout.record_refund(wrong.payment_intent_id, 100)
        listed = panel(build_dashboard(admin), "Payments needing attention")
        self.assertEqual([row.url for row in listed.rows], [f"/admin/billing/payment/{disputed.pk}/change/"])

    def test_the_home_page_shows_the_lists(self):
        self.payments_on()
        user = User.objects.create_user(username="administrator", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="administrator", password="testpass123")
        self.paid(self.sent(self.request(full_name="Waiting Rider")))
        home = self.client.get("/admin/")
        self.assertContains(home, "Paid, waiting to be scheduled")
        self.assertContains(home, "Waiting Rider")
        self.assertContains(home, "Payments needing attention")
        self.assertContains(home, "No payment needs attention.")


# --- With payment off, nothing asks Stripe -------------------------------------------


class PaymentOffTests(FlowTestCase):
    def test_no_page_asks_stripe_while_payment_is_off(self):
        quote = self.sent()
        self.assertContains(self.client.get(self.pay_path(quote)), GENERIC)
        self.assertContains(self.press(quote), GENERIC)
        self.assertEqual(self.client.get("/pay/return/", {"session_id": "cs_test_someoneelses"}).status_code, 302)
        with self.settings(STRIPE_WEBHOOK_SECRET=""):
            self.assertEqual(self.client.post(WEBHOOK, data=b"{}", content_type="application/json").status_code, 503)
        with self.captureOnCommitCallbacks(execute=True):
            rider_services.cancel_request(RiderAccount(User.objects.create_user(username="rider-test")),
                                          quote.trip_request)
        self.assertEqual(checkout.daily_check(), "off")
        build_dashboard(staff("administrator", "Administrator"))
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PaymentEvent.objects.exists())

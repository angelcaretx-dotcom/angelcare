"""
The base for every test that touches online payment (ADR 0050).

A test must never hold a real Stripe key and never reach the network.
`PaymentTestMixin` makes both true for each test: it blanks the Stripe
settings, puts the in-memory `FakeProvider` in place of Stripe, and makes
any real HTTP call fail the test loudly.

Online payment starts OFF in a test, exactly as in production. Call
`self.payments_on()` for a server that is configured with the
administrator's switch on.

    class MyTests(PaymentTestCase):
        def test_something(self):
            self.payments_on()
            payment = self.make_payment(quote)
            ...
            self.provider.pay(payment.session_id)

Mix `PaymentTestMixin` in front of another base when a test needs both:
`class MyTests(PaymentTestMixin, QuoteTestCase)`.
"""

from unittest import mock

from django.test import TestCase, override_settings

from . import payments
from .models import Payment, PaymentTerms

# No key, no webhook secret, testing not allowed, not configured.
BLANK_STRIPE_SETTINGS = {
    "STRIPE_SECRET_KEY": "",
    "STRIPE_WEBHOOK_SECRET": "",
    "STRIPE_ALLOW_TEST": False,
    "PAYMENTS_CONFIGURED": False,
}


def _no_network(*args, **kwargs):
    raise AssertionError("A payment test tried to reach the network. Tests use the FakeProvider only.")


class PaymentTestMixin:
    def setUp(self):
        blank = override_settings(**BLANK_STRIPE_SETTINGS)
        blank.enable()
        self.addCleanup(blank.disable)
        # Every HTTP call `requests` makes ends here, whoever makes it.
        guard = mock.patch("requests.adapters.HTTPAdapter.send", _no_network)
        guard.start()
        self.addCleanup(guard.stop)
        self.provider = payments.FakeProvider()
        payments.substitute(self.provider)
        self.addCleanup(payments.substitute, None)
        super().setUp()

    def payments_on(self, **texts) -> PaymentTerms:
        """A configured server with the administrator's switch on. `texts` reword the two customer texts."""
        configured = override_settings(PAYMENTS_CONFIGURED=True)
        configured.enable()
        self.addCleanup(configured.disable)
        row = PaymentTerms.load()
        row.enabled = True
        for name, value in texts.items():
            setattr(row, name, value)
        row.save()
        return row

    def make_payment(self, quote, **overrides) -> Payment:
        """An open payment for a quote, with a session at the fake provider, as the pay page would leave it."""
        attempt = overrides.pop("attempt", quote.payments.count() + 1)
        amount_cents = overrides.pop("amount_cents", payments.to_cents(quote.amount))
        expires_at = overrides.pop("expires_at", payments.session_expiry())
        session = self.provider.create_session(
            amount_cents=amount_cents, reference=payments.reference(quote.trip_request_id), quote_id=quote.pk,
            idempotency_key=f"{quote.pk}-{attempt}", success_url=payments.success_url(),
            cancel_url=payments.cancel_url(), expires_at=expires_at,
        )
        fields = {"quote": quote, "attempt": attempt, "amount_cents": amount_cents, "session_id": session.id,
                  "session_url": session.url, "expires_at": session.expires_at, "livemode": session.livemode}
        fields.update(overrides)
        return Payment.objects.create(**fields)


class PaymentTestCase(PaymentTestMixin, TestCase):
    """A TestCase with the fake provider in place and the Stripe settings blank."""

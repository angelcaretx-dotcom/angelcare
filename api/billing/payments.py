"""
Paying an emailed price online (ADR 0050): the payment vendor behind an
interface, and the small questions the rest of the system asks about
payments.

The vendor is Stripe Checkout. Like the email vendor
(`notifications/providers/`), it sits behind an interface: everything else
talks to `get_provider()`, never to Stripe, so the tests and local
development run on an in-memory provider and no test can reach the
network.

Three rules this module keeps for everyone who calls it:

- **Off unless everything is in place.** `payments_on()` is true only when
  Stripe is configured on the server AND the administrator switched online
  payment on. Anything missing, including the settings table itself, means
  off.
- **Only this goes to Stripe:** the amount, the currency, the quote's id
  and one product name, "Transportation booking, reference #XXXXXXXX".
  `create_session` has no argument that could carry anything else, and it
  refuses a reference that isn't that shape.
- **Nothing secret is logged or raised.** A failure is logged as the step,
  the kind of error, the HTTP status and Stripe's own request id and error
  code. `PaymentError`'s message is safe to show staff.
"""

import logging
import re
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import requests
from django.conf import settings
from django.core import signing
from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from . import stripe_keys
from .models import (
    DEFAULT_REFUND_POLICY,
    DEFAULT_WHAT_PAYING_MEANS,
    Payment,
    PaymentStatus,
    PaymentTerms,
)

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
# Stripe's API version, pinned so a change on Stripe's side can't alter what these calls
# mean. The webhook endpoint in Stripe must be created on the same version.
STRIPE_VERSION = "2026-11-18.endive"
# The only place a customer is ever sent to pay.
CHECKOUT_PREFIX = "https://checkout.stripe.com/"
# Seconds: (to connect, to read the answer).
TIMEOUT = (3, 8)
# Stripe's smallest card charge in US dollars. A smaller total gets no payment link.
MIN_CENTS = 50
CURRENCY = "usd"
# How long one attempt stays open at Stripe (its minimum is 30 minutes, its maximum 24 hours).
SESSION_MINUTES = 60

# The pay link in the price email: `signing.dumps(str(quote.pk), salt=...)`. Whoever holds
# the email holds the link; it stops working after 30 days (and at the pickup time, which
# the page checks).
PAY_TOKEN_SALT = "quote-pay"
PAY_TOKEN_MAX_AGE = timedelta(days=30)

UNAVAILABLE = "Online payment is not available right now. Please try again in a few minutes."
NOT_SET_UP = "Online payment is not set up on the server."

_REFERENCE = re.compile(r"#[0-9A-F]{8}")
_SESSION_ID = re.compile(r"cs_[A-Za-z0-9_]{1,250}")
_PLACEHOLDER = "{CHECKOUT_SESSION_ID}"


class PaymentError(Exception):
    """
    Any failure talking to the payment provider. `str(error)` is safe to
    show staff: it never holds a key, a web address, a request or a reply.
    The other fields are for the log line and say only what Stripe itself
    calls the failure.
    """

    def __init__(self, message: str = UNAVAILABLE, *, step: str = "", http_status: int | None = None,
                 code: str = "", request_id: str = "", param: str = ""):
        super().__init__(message)
        self.step = step
        self.http_status = http_status
        self.code = code
        self.request_id = request_id
        self.param = param


@dataclass(frozen=True)
class Session:
    """A payment page Stripe opened for one attempt."""

    id: str
    url: str
    expires_at: datetime
    livemode: bool


@dataclass(frozen=True)
class SessionState:
    """What Stripe says about a session right now. This, not a webhook's word, decides whether a price is paid."""

    id: str
    status: str  # open / complete / expired
    payment_status: str  # paid / unpaid / no_payment_required
    mode: str  # payment
    amount_total: int | None  # cents
    currency: str  # lowercase, e.g. "usd"
    payment_intent: str | None
    livemode: bool
    metadata_quote: str  # the quote id this system put in the session's metadata


# --- Money and names ---------------------------------------------------------


def to_cents(amount) -> int:
    """A dollar amount as whole cents, exactly. Refuses anything that isn't a whole number of cents."""
    if isinstance(amount, float):
        raise TypeError("Money is a Decimal, never a float.")
    cents = Decimal(amount) * 100
    if cents != cents.to_integral_value() or cents < 0:
        raise ValueError("Not a whole, non-negative number of cents.")
    return int(cents)


def payable(amount) -> bool:
    """Can this total be paid by card? Stripe takes nothing under 50 cents, so a smaller total gets no link."""
    return to_cents(amount) >= MIN_CENTS


def reference(trip_request_id) -> str:
    """The reference customers and staff quote, e.g. "#3FA9C2D1": the first eight characters of the request's id."""
    return f"#{str(trip_request_id)[:8].upper()}"


def product_name(ref: str) -> str:
    """The one line of text Stripe is given and shows the payer. It names no person, place, date or service."""
    return f"Transportation booking, reference {ref}"


def session_expiry(now: datetime | None = None) -> datetime:
    """When a session opened now closes if unpaid, to the whole second (Stripe is sent whole seconds)."""
    return (now or timezone.now()).replace(microsecond=0) + timedelta(minutes=SESSION_MINUTES)


# --- The provider interface --------------------------------------------------


class PaymentProvider(ABC):
    """
    What the system needs from a payment vendor. `livemode` says which kind
    of money this provider moves: True for real payments, False for test
    ones. A session whose own `livemode` differs must never be applied.
    """

    livemode: bool = False

    @abstractmethod
    def create_session(self, *, amount_cents: int, reference: str, quote_id, idempotency_key: str,
                       success_url: str, cancel_url: str, expires_at: datetime) -> Session:
        """
        Open a payment page for one attempt. Calling it again with the same
        `idempotency_key` and the same values gives the same session back,
        so a retry must pass the `expires_at` it stored, not a fresh one.
        """

    @abstractmethod
    def retrieve_session(self, session_id: str) -> SessionState:
        """Ask the vendor where a session stands."""

    @abstractmethod
    def expire_session(self, session_id: str) -> SessionState:
        """Close an open session so it can no longer be paid. Raises if it is already closed or paid."""

    @staticmethod
    def check_create(*, amount_cents, reference, quote_id, idempotency_key, success_url, cancel_url, expires_at):
        """The rules every provider applies before opening a session. Raises PaymentError with a plain reason."""
        step = "create_session"
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents < MIN_CENTS:
            raise PaymentError("This amount is too small to pay by card.", step=step)
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise PaymentError("The reference number isn't valid, so nothing was sent.", step=step)
        try:
            uuid.UUID(str(quote_id))
        except ValueError:
            raise PaymentError("The price's id isn't valid, so nothing was sent.", step=step) from None
        if not idempotency_key or len(idempotency_key) > 255:
            raise PaymentError("The attempt has no number, so nothing was sent.", step=step)
        if _PLACEHOLDER not in (success_url or "") or not cancel_url:
            raise PaymentError("The return addresses aren't valid, so nothing was sent.", step=step)
        if not isinstance(expires_at, datetime) or timezone.is_naive(expires_at) or expires_at <= timezone.now():
            raise PaymentError("The closing time isn't valid, so nothing was sent.", step=step)

    @staticmethod
    def check_session_id(session_id, step: str) -> str:
        """A session id goes into a web address, and can arrive from a browser: only Stripe's own shape passes."""
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise PaymentError("That isn't a payment this system knows.", step=step)
        return session_id


# --- Stripe ------------------------------------------------------------------


def _token(value, limit: int = 80) -> str:
    """A code or id from Stripe, reduced to plain characters so it can go in a log line or a message."""
    return re.sub(r"[^A-Za-z0-9_.\[\]-]", "", str(value or ""))[:limit]


class StripeProvider(PaymentProvider):
    """
    Stripe Checkout over HTTPS with `requests` (no Stripe library). The key
    is sent only in the Authorization header and is never logged, stored or
    put in an error.
    """

    def __init__(self, secret_key: str):
        mode = stripe_keys.key_mode(secret_key)
        if not mode:
            raise PaymentError(NOT_SET_UP, step="setup")
        self._key = secret_key
        self.livemode = mode == stripe_keys.LIVE

    def __repr__(self) -> str:  # never the key
        return f"<StripeProvider {'live' if self.livemode else 'test'}>"

    def create_session(self, *, amount_cents, reference, quote_id, idempotency_key, success_url, cancel_url,
                       expires_at) -> Session:
        self.check_create(amount_cents=amount_cents, reference=reference, quote_id=quote_id,
                          idempotency_key=idempotency_key, success_url=success_url, cancel_url=cancel_url,
                          expires_at=expires_at)
        body = self._call("create_session", "POST", "/checkout/sessions",
                          data=self.create_params(amount_cents=amount_cents, reference=reference, quote_id=quote_id,
                                                  success_url=success_url, cancel_url=cancel_url,
                                                  expires_at=expires_at),
                          idempotency_key=idempotency_key)
        step = "create_session"
        session_id, url, expires = body.get("id"), body.get("url"), body.get("expires_at")
        if not (isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id)):
            raise self._unreadable(step)
        # The customer is only ever sent to Stripe's own payment page.
        if not (isinstance(url, str) and url.startswith(CHECKOUT_PREFIX)):
            raise self._unreadable(step)
        if isinstance(expires, bool) or not isinstance(expires, int):
            raise self._unreadable(step)
        livemode = body.get("livemode") is True
        if livemode != self.livemode:
            logger.warning("Stripe call refused: step=%s error=LivemodeMismatch", step)
            raise PaymentError(UNAVAILABLE, step=step)
        return Session(id=session_id, url=url, expires_at=datetime.fromtimestamp(expires, tz=dt_timezone.utc),
                       livemode=livemode)

    @staticmethod
    def create_params(*, amount_cents, reference, quote_id, success_url, cancel_url, expires_at) -> dict:
        """
        Exactly what Stripe is sent to open a session, as form fields. A
        one-off card payment for one line: no customer email (Stripe's page
        asks the payer), no promotion codes, no tax, no address or phone.
        """
        name = product_name(reference)
        quote = str(quote_id)
        return {
            "mode": "payment",
            "line_items[0][price_data][currency]": CURRENCY,
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": name,
            "line_items[0][quantity]": "1",
            "client_reference_id": quote,
            "metadata[quote]": quote,
            # A session's metadata isn't copied to the payment, and refunds and disputes arrive on the payment.
            "payment_intent_data[metadata][quote]": quote,
            "payment_intent_data[description]": name,
            # Cards only (this replaced `payment_method_types`, which Stripe rejects from 2026-09-30).
            "allowed_payment_method_types[0]": "card",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": str(int(expires_at.timestamp())),
        }

    def retrieve_session(self, session_id: str) -> SessionState:
        step = "retrieve_session"
        self.check_session_id(session_id, step)
        return self._state(step, session_id, self._call(step, "GET", f"/checkout/sessions/{session_id}"))

    def expire_session(self, session_id: str) -> SessionState:
        step = "expire_session"
        self.check_session_id(session_id, step)
        return self._state(step, session_id, self._call(step, "POST", f"/checkout/sessions/{session_id}/expire"))

    def _state(self, step: str, session_id: str, body: dict) -> SessionState:
        if body.get("id") != session_id:
            raise self._unreadable(step)
        amount = body.get("amount_total")
        if isinstance(amount, bool) or not (amount is None or isinstance(amount, int)):
            raise self._unreadable(step)
        intent = body.get("payment_intent")
        if isinstance(intent, dict):  # only if someone asked Stripe to expand it
            intent = intent.get("id")
        metadata = body.get("metadata")
        return SessionState(
            id=session_id,
            status=str(body.get("status") or ""),
            payment_status=str(body.get("payment_status") or ""),
            mode=str(body.get("mode") or ""),
            amount_total=amount,
            currency=str(body.get("currency") or "").lower(),
            payment_intent=intent if isinstance(intent, str) and intent else None,
            livemode=body.get("livemode") is True,
            metadata_quote=str((metadata if isinstance(metadata, dict) else {}).get("quote") or ""),
        )

    @staticmethod
    def _unreadable(step: str) -> PaymentError:
        logger.warning("Stripe call failed: step=%s error=UnexpectedAnswer", step)
        return PaymentError(UNAVAILABLE, step=step)

    def _call(self, step: str, method: str, path: str, *, data: dict | None = None,
              idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._key}", "Stripe-Version": STRIPE_VERSION}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = requests.request(method, f"{STRIPE_API}{path}", data=data, headers=headers,
                                        timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc:
            # Never str(exc): a requests error can quote the address it was calling.
            logger.warning("Stripe call failed: step=%s error=%s", step, type(exc).__name__)
            raise PaymentError(UNAVAILABLE, step=step) from None
        status = response.status_code
        request_id = _token(response.headers.get("Request-Id"))
        try:
            body = response.json()
        except ValueError:
            body = None
        if 200 <= status < 300 and isinstance(body, dict):
            return body
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        code = _token(error.get("code") or error.get("type"))
        logger.warning("Stripe call failed: step=%s error=HTTPError status=%s code=%s request_id=%s",
                       step, status, code or "-", request_id or "-")
        if 400 <= status < 500 and status != 429:
            # Stripe refused the request itself: trying again won't help, so say what it called the problem.
            message = (f"Stripe did not accept the request (HTTP {status}"
                       f"{f', {code}' if code else ''}).{f' Stripe request {request_id}.' if request_id else ''}")
        else:
            message = UNAVAILABLE
        raise PaymentError(message, step=step, http_status=status, code=code, request_id=request_id,
                           param=_token(error.get("param")))


# --- The in-memory provider --------------------------------------------------


class FakeProvider(PaymentProvider):
    """
    A provider that keeps its sessions in memory and behaves like Stripe
    where it matters: the same idempotency key gives the same session, an
    unknown session is an error, only an open session can be expired, and a
    session closes by itself at its expiry time. Used by every test, and by
    local development when DEBUG is on and no key is set.

    For tests: `calls` lists what was asked, in order; `pay()` and `set()`
    change what "Stripe" would answer; `down = True` makes every call fail.
    """

    def __init__(self, *, livemode: bool = False):
        self.livemode = livemode
        self.sessions: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.down = False
        self._by_key: dict[str, tuple[dict, str]] = {}

    def _begin(self, step: str, **what) -> None:
        self.calls.append((step, what))
        if self.down:
            raise PaymentError(UNAVAILABLE, step=step)

    def create_session(self, *, amount_cents, reference, quote_id, idempotency_key, success_url, cancel_url,
                       expires_at) -> Session:
        asked = {"amount_cents": amount_cents, "reference": reference, "quote_id": str(quote_id),
                 "idempotency_key": idempotency_key, "success_url": success_url, "cancel_url": cancel_url,
                 "expires_at": expires_at}
        self.check_create(**{**asked, "quote_id": quote_id})
        self._begin("create_session", **asked)
        if idempotency_key in self._by_key:
            first, session_id = self._by_key[idempotency_key]
            if first != asked:
                raise PaymentError("Stripe did not accept the request (HTTP 400, idempotency_error).",
                                   step="create_session", http_status=400, code="idempotency_error")
        else:
            mode = "live" if self.livemode else "test"
            session_id = f"cs_{mode}_fake{len(self.sessions) + 1:04d}"
            self.sessions[session_id] = {
                "status": "open", "payment_status": "unpaid", "mode": "payment", "amount_total": amount_cents,
                "currency": CURRENCY, "payment_intent": None, "livemode": self.livemode,
                "metadata_quote": str(quote_id), "url": f"{CHECKOUT_PREFIX}c/pay/{session_id}",
                "expires_at": expires_at,
            }
            self._by_key[idempotency_key] = (asked, session_id)
        stored = self.sessions[session_id]
        return Session(id=session_id, url=stored["url"], expires_at=stored["expires_at"], livemode=stored["livemode"])

    def _known(self, step: str, session_id: str) -> dict:
        self.check_session_id(session_id, step)
        self._begin(step, session_id=session_id)
        stored = self.sessions.get(session_id)
        if stored is None:
            raise PaymentError("Stripe did not accept the request (HTTP 404, resource_missing).",
                               step=step, http_status=404, code="resource_missing")
        if stored["status"] == "open" and stored["expires_at"] <= timezone.now():
            stored["status"] = "expired"
        return stored

    def _answer(self, session_id: str, stored: dict) -> SessionState:
        return SessionState(id=session_id, **{name: stored[name] for name in (
            "status", "payment_status", "mode", "amount_total", "currency", "payment_intent", "livemode",
            "metadata_quote")})

    def retrieve_session(self, session_id: str) -> SessionState:
        return self._answer(session_id, self._known("retrieve_session", session_id))

    def expire_session(self, session_id: str) -> SessionState:
        stored = self._known("expire_session", session_id)
        if stored["status"] != "open":
            raise PaymentError("Stripe did not accept the request (HTTP 400).", step="expire_session",
                               http_status=400)
        stored["status"] = "expired"
        return self._answer(session_id, stored)

    # What a test does in the customer's place.

    def pay(self, session_id: str, **changes) -> None:
        """The customer paid: the session is complete and paid, with a payment id."""
        number = session_id.rsplit("fake", 1)[-1]
        self.set(session_id, **{"status": "complete", "payment_status": "paid", "payment_intent": f"pi_fake{number}",
                                **changes})

    def set(self, session_id: str, **changes) -> None:
        """Change what "Stripe" answers for a session, e.g. `amount_total=1` or `livemode=True`."""
        self.sessions[session_id].update(changes)


# --- Which provider, and is online payment on? -------------------------------

_substitute: PaymentProvider | None = None
_local_fake: FakeProvider | None = None


def substitute(provider: PaymentProvider | None) -> None:
    """Make `get_provider()` return this provider (the tests' fake); None puts the real choice back."""
    global _substitute
    _substitute = provider


@contextmanager
def substituted(provider: PaymentProvider):
    previous = _substitute
    substitute(provider)
    try:
        yield provider
    finally:
        substitute(previous)


def _local_development() -> bool:
    """DEBUG on and no key: the in-memory provider stands in, so the flow can be clicked through locally."""
    return bool(settings.DEBUG) and not settings.STRIPE_SECRET_KEY


def get_provider() -> PaymentProvider:
    """
    The provider to use right now: a substitute if a test set one, the
    in-memory one for local development, otherwise Stripe. Raises
    PaymentError when Stripe isn't fully and correctly configured, so a
    half-configured server can never open a payment page.
    """
    global _local_fake
    if _substitute is not None:
        return _substitute
    if _local_development():
        if _local_fake is None:
            _local_fake = FakeProvider()
        return _local_fake
    usable = settings.PAYMENTS_CONFIGURED and stripe_keys.configured(
        settings.STRIPE_SECRET_KEY, settings.STRIPE_WEBHOOK_SECRET,
        debug=settings.DEBUG, allow_test=settings.STRIPE_ALLOW_TEST,
    )
    if not usable:
        raise PaymentError(NOT_SET_UP, step="setup")
    return StripeProvider(settings.STRIPE_SECRET_KEY)


def configured() -> bool:
    """Is the server set up for online payment? No database read, so it is safe on any page."""
    return bool(settings.PAYMENTS_CONFIGURED) or _local_development()


def webhook_secrets() -> list[str]:
    """The webhook signing secrets to accept: one, or two while a secret is being rolled. Empty means "not set"."""
    return stripe_keys.webhook_secrets(settings.STRIPE_WEBHOOK_SECRET)


def test_mode() -> bool:
    """True when payments made now would be Stripe TEST payments: the admin says so on its payment pages."""
    if not configured():
        return False
    try:
        return not get_provider().livemode
    except PaymentError:
        return False


def _table_missing(exc: DatabaseError) -> bool:
    """Is this "the table doesn't exist"? PostgreSQL says so with code 42P01, SQLite in words."""
    cause = exc.__cause__
    code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    if code:
        return code == "42P01"
    return "no such table" in str(exc).lower()


def safe_read(read, default):
    """
    Run one read of the payment tables for a page that must not break
    because of them. If the tables aren't there yet (this code went live
    before its database update), the answer is `default`. The read runs in
    its own savepoint, so that failure leaves the caller's transaction
    usable. Any other database error is raised as usual: a real fault must
    never read as "not paid".
    """
    try:
        with transaction.atomic():
            return read()
    except DatabaseError as exc:
        if not _table_missing(exc):
            raise
        logger.warning("A payment table is missing (database update not run yet): error=%s", type(exc).__name__)
        return default


@dataclass(frozen=True)
class Terms:
    """The administrator's payment settings, as plain values. The defaults are the safe ones: switched off."""

    enabled: bool = False
    refund_policy: str = DEFAULT_REFUND_POLICY
    what_paying_means: str = DEFAULT_WHAT_PAYING_MEANS


def terms() -> Terms:
    """
    The payment settings (Billing > Payment settings). Safe defaults,
    switched off, when the row hasn't been made yet or its table doesn't
    exist. A text left blank falls back to the owner's rule. Never writes.
    """
    row = safe_read(lambda: PaymentTerms.objects.order_by("pk").first(), None)
    if row is None:
        return Terms()
    return Terms(
        enabled=bool(row.enabled),
        refund_policy=(row.refund_policy or "").strip() or DEFAULT_REFUND_POLICY,
        what_paying_means=(row.what_paying_means or "").strip() or DEFAULT_WHAT_PAYING_MEANS,
    )


def payments_on() -> bool:
    """
    Is online payment offered right now? Only when the server is configured
    for it AND the administrator's switch is on. With this false, a price
    email is exactly what it was before online payment existed.
    """
    return configured() and terms().enabled


# --- Is it paid? --------------------------------------------------------------


def holding(queryset):
    """
    Of these payments, the paid ones that still hold the customer's money.
    A payment refunded in full in Stripe no longer counts as paid: that is
    what lets the office refund a ride and then email a different price.
    A partly refunded payment still counts.
    """
    return queryset.filter(status=PaymentStatus.PAID).exclude(
        paid_cents__isnull=False, refunded_cents__gte=F("paid_cents")
    )


def paid_payment(trip_request) -> Payment | None:
    """The payment that paid this request (through any of its quotes), or None. Safe on any page."""
    return safe_read(
        lambda: holding(Payment.objects.filter(quote__trip_request=trip_request))
        .select_related("quote").order_by("paid_at", "created_at").first(),
        None,
    )


def request_paid(trip_request) -> bool:
    """A request is paid when any of its quotes is."""
    return paid_payment(trip_request) is not None


def quote_paid(quote) -> bool:
    """A quote is paid when it has a payment with status Paid (that was not refunded in full)."""
    return safe_read(lambda: holding(Payment.objects.filter(quote=quote)).exists(), False)


# --- The link in the price email ---------------------------------------------


def pay_token(quote) -> str:
    """The signed token in a price email's pay link. Its characters are letters, digits and - _ : only."""
    return signing.dumps(str(quote.pk), salt=PAY_TOKEN_SALT)


def read_pay_token(token: str) -> str | None:
    """The quote id a pay link carries, or None when the link is forged, damaged or older than 30 days."""
    try:
        value = signing.loads(token, salt=PAY_TOKEN_SALT, max_age=PAY_TOKEN_MAX_AGE)
        return str(uuid.UUID(value))
    except (signing.BadSignature, ValueError, TypeError, AttributeError):
        return None


def pay_url(quote) -> str:
    """The pay link as the customer sees it: on the website, which hands it to this server."""
    return f"{settings.WEBSITE_URL}/pay/{pay_token(quote)}"


def success_url() -> str:
    """Where Stripe sends the payer afterwards. Stripe itself fills in the session id."""
    return f"{settings.SITE_URL}/pay/return/?session_id={_PLACEHOLDER}"


def cancel_url() -> str:
    """Where Stripe's "back" link leads."""
    return f"{settings.WEBSITE_URL}/payment/cancelled"

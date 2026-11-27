"""
Paying an emailed price online, step by step (ADR 0050).

`billing/payments.py` holds the vendor and the small questions ("is
online payment on?", "is this request paid?"). This module is what
happens to a payment: starting one from the link in the price email,
recording what Stripe says about it, closing the ones that must no longer
be paid, and telling the office and the customer.

The rules it keeps:

- **One open payment page per price.** A second press of the button, or a
  second browser, is sent to the page that is already open. A new page is
  only opened once the last one has closed.
- **Stripe's own answer decides.** `apply_session` is the one place a
  payment is marked paid, and it is only ever given what Stripe answered
  when asked about the session: never a webhook's contents.
- **Money that doesn't fit is never lost and never accepted quietly.** A
  payment that arrives for a replaced price, a cancelled request, the
  wrong amount, or on top of another payment becomes "needs attention":
  the office is told, and a person decides (usually a refund in Stripe).
- **The database first, emails after.** A status changes in one
  conditional update, so two deliveries of the same news change it once;
  the emails go out after the change is saved, each one once.
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from audit.services import record_change
from notifications.services import NotificationService
from transportation.models import ReturnRide, TripRequest, TripRequestStatus

from . import payments, pricing
from .models import Payment, PaymentStatus, Quote
from .payments import PaymentError

logger = logging.getLogger(__name__)

# A ride that isn't happening can't be paid for.
CLOSED = (TripRequestStatus.CANCELLED, TripRequestStatus.DECLINED)

# What the page says when a pay link can't be used. Only these four reasons are ever given:
# each tells the holder of the email something about their own booking and nothing else.
ALREADY_PAID = "This price has already been paid. Thank you."
REPLACED = "We sent you a newer price for this ride. Please use the link in our most recent email."
TOO_LATE = "The pickup time for this ride has passed."
CANCELLED = "This ride request was cancelled."

# The owner's rule about waiting (2026-11-26), shown wherever a ride with a waiting driver is paid for.
WAITING_RULE = (
    "If the driver waits longer than the waiting time this price covers, the extra waiting is billed "
    "after the ride. If the wait is shorter, nothing is refunded."
)

# A payment row made before Stripe answered is tried again with the same closing time, and
# Stripe refuses a closing time less than 30 minutes away: past this point a new attempt starts.
RETRY_NEEDS = timedelta(minutes=35)
# The daily check looks this far back.
RECHECK_DAYS = 3
RECHECK_LIMIT = 50
RECHECK_SECONDS = 25

# What `apply_session` answers when the session isn't one of ours.
UNKNOWN = "unknown"
# The statuses that mean money reached Stripe.
MONEY_ARRIVED = (PaymentStatus.PAID, PaymentStatus.NEEDS_ATTENTION)
NOT_FINAL = (PaymentStatus.OPEN, PaymentStatus.EXPIRED, PaymentStatus.FAILED)


# --- Can this price be paid right now? ---------------------------------------


def newest_delivered(trip_request_id) -> Quote | None:
    """The price the customer was last told: the newest quote whose email went out."""
    return Quote.objects.filter(trip_request_id=trip_request_id, delivered=True).order_by("-created_at").first()


def money_held(trip_request_id, *, exclude=None) -> bool:
    """
    Has money already arrived for this request and not been refunded in
    full? A paid payment, or one that needs attention: either way a second
    payment would charge the customer twice.
    """
    rows = Payment.objects.filter(quote__trip_request_id=trip_request_id, status__in=MONEY_ARRIVED)
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    return rows.exclude(paid_cents__isnull=False, refunded_cents__gte=F("paid_cents")).exists()


def refusal(quote, trip_request, now=None) -> str | None:
    """
    Why this price can't be paid right now: one of the four reasons above,
    or "" for "say nothing more than 'please call us'". None when it can be
    paid. Every rule of the pay page is here, so the page that shows the
    button and the press of the button always agree.
    """
    now = now or timezone.now()
    if not payments.payments_on():
        return ""
    if quote is None or trip_request is None or not quote.delivered:
        return ""
    if payments.request_paid(trip_request):
        return ALREADY_PAID
    if trip_request.status == TripRequestStatus.CANCELLED:
        return CANCELLED
    if trip_request.status in CLOSED:
        return ""
    if money_held(trip_request.pk):
        # A payment that needs attention holds the customer's money: no second charge on top of it.
        return ""
    newest = newest_delivered(trip_request.pk)
    if newest is None or newest.pk != quote.pk:
        return REPLACED
    if trip_request.requested_datetime <= now:
        return TOO_LATE
    try:
        if not payments.payable(quote.amount):
            return ""
    except (TypeError, ValueError, ArithmeticError):
        return ""
    # A Medicaid or broker rider is never charged (ADR 0047), whatever was emailed before the link was made.
    payer = pricing.quote_payer(trip_request)
    if payer is not None and payer.payer_type in pricing.PROGRAM_PAYER_TYPES:
        return ""
    return None


@dataclass(frozen=True)
class Offer:
    """What the page before payment shows. `refusal` is None when the price can be paid."""

    refusal: str | None
    reference: str = ""
    total: str = ""
    what_paying_means: str = ""
    refund_policy: str = ""
    waiting: str = ""


def waiting_rule(trip_request) -> str:
    """The sentence about waiting, for a ride where the driver waits; otherwise nothing."""
    return WAITING_RULE if trip_request.return_ride == ReturnRide.WAIT else ""


def look(quote_id) -> Offer:
    """
    What the pay page shows for a link, without changing anything and
    without asking Stripe: mail scanners open links, and so do people who
    only want to read.
    """
    if not payments.payments_on():
        return Offer(refusal="")
    quote = Quote.objects.filter(pk=quote_id).select_related("trip_request__passenger__payer").first()
    trip_request = quote.trip_request if quote else None
    why = refusal(quote, trip_request)
    if why is not None:
        return Offer(refusal=why)
    terms = payments.terms()
    return Offer(
        refusal=None, reference=payments.reference(trip_request.pk), total=f"${quote.amount:,.2f}",
        what_paying_means=terms.what_paying_means, refund_policy=terms.refund_policy,
        waiting=waiting_rule(trip_request),
    )


# --- The price email's pay link ----------------------------------------------


def pay_terms(amount):
    """
    The payment settings, when a price of this amount is to carry a pay
    link; None when it isn't (online payment is off, or the total is under
    Stripe's 50-cent minimum). Asked before the quote is saved, so that a
    problem reading the settings can never leave a half-sent price.
    """
    if not payments.configured() or not payments.payable(amount):
        return None
    terms = payments.terms()
    return terms if terms.enabled else None


def pay_context(quote, terms) -> dict | None:
    """What the price email says about paying online. Reads nothing: `terms` came from `pay_terms`."""
    if terms is None:
        return None
    return {
        "url": payments.pay_url(quote),
        "what_paying_means": terms.what_paying_means,
        "refund_policy": terms.refund_policy,
        "waiting": waiting_rule(quote.trip_request),
    }


# --- Starting a payment (the button on the pay page) --------------------------


@dataclass(frozen=True)
class Started:
    """The result of pressing "Continue to secure payment": exactly one of the three is set."""

    url: str = ""  # Stripe's payment page: send the customer there
    refusal: str | None = None  # this link can't be used (see `refusal`)
    unavailable: bool = False  # Stripe couldn't be reached: nothing was opened


def _lock_request(trip_request_id) -> TripRequest | None:
    """The request row, locked until the transaction ends. Everything that decides about a payment takes it first."""
    return TripRequest.objects.select_for_update().filter(pk=trip_request_id).first()


def start(quote_id) -> Started:
    """
    Send the customer to Stripe's page for this price: the page that is
    already open if there is one, a new one otherwise.

    In three steps, so that no database lock is held while Stripe is asked:
    1. With the request locked: check every rule, then find the open
       payment of this price or save a new one (with its closing time).
    2. Ask Stripe for the page, under the attempt's own number. Asking
       twice for the same attempt gives the same page, so two browsers,
       or a retry after a timeout, can never open two.
    3. With the request locked again: store the page, and check that
       nothing changed meanwhile (a cancellation, a newer price).
    """
    now = timezone.now()
    request_id = Quote.objects.filter(pk=quote_id).values_list("trip_request_id", flat=True).first()
    closed_early: list[str] = []
    with transaction.atomic():
        trip_request = _lock_request(request_id) if request_id else None
        quote = Quote.objects.filter(pk=quote_id).first()
        why = refusal(quote, trip_request, now)
        if why is not None:
            return Started(refusal=why)
        current = None
        for row in quote.payments.filter(status=PaymentStatus.OPEN).order_by("-attempt"):
            if current is None and _usable(row, now):
                current = row
                continue
            # Closed at Stripe by now, or never opened there: it makes way for a new attempt.
            _set_status(row.pk, [PaymentStatus.OPEN], PaymentStatus.EXPIRED)
            if row.session_id:
                closed_early.append(row.session_id)
        if current is None:
            last = quote.payments.aggregate(last=Max("attempt"))["last"] or 0
            current = Payment.objects.create(
                quote=quote, attempt=last + 1, amount_cents=payments.to_cents(quote.amount),
                currency=payments.CURRENCY, expires_at=payments.session_expiry(now),
            )
        reference = payments.reference(trip_request.pk)
    expire_at_stripe(closed_early)

    if current.session_id and current.session_url:
        return _send_to(current.session_url)

    try:
        session = payments.get_provider().create_session(
            amount_cents=current.amount_cents, reference=reference, quote_id=quote.pk,
            idempotency_key=f"{quote.pk}-{current.attempt}", success_url=payments.success_url(),
            cancel_url=payments.cancel_url(), expires_at=current.expires_at,
        )
    except PaymentError as exc:
        if exc.http_status is not None and 400 <= exc.http_status < 500 and exc.http_status != 429:
            # Stripe refused this attempt itself: asking again under the same number can't work.
            _set_status(current.pk, [PaymentStatus.OPEN], PaymentStatus.FAILED, session_id__isnull=True)
        # Otherwise the row stays: the next press asks again under the same number and closing time.
        return Started(unavailable=True)

    with transaction.atomic():
        trip_request = _lock_request(request_id)
        Payment.objects.filter(pk=current.pk, session_id__isnull=True).update(
            session_id=session.id, session_url=session.url, livemode=session.livemode, updated_at=timezone.now(),
        )
        current.refresh_from_db()
        quote = Quote.objects.filter(pk=quote_id).first()
        why = refusal(quote, trip_request)
        if why is None and current.status != PaymentStatus.OPEN:
            why = ""
        if why is not None:
            # Cancelled, repriced or paid while Stripe was being asked: this page must not be paid.
            _set_status(current.pk, [PaymentStatus.OPEN], PaymentStatus.EXPIRED)
            transaction.on_commit(lambda: expire_at_stripe([session.id]))
            return Started(refusal=why)
    return _send_to(current.session_url)


def _usable(row, now) -> bool:
    """Can this open payment row still be used for the customer's next press?"""
    if row.expires_at is None:
        return False
    if row.session_id and row.session_url:
        return row.expires_at > now
    return row.expires_at - now >= RETRY_NEEDS


def _send_to(url: str) -> Started:
    # The customer is only ever sent to Stripe's own payment page.
    if not url.startswith(payments.CHECKOUT_PREFIX):
        logger.warning("Payment start refused: step=redirect error=NotStripesPage")
        return Started(unavailable=True)
    return Started(url=url)


def _set_status(payment_id, from_statuses, to_status, **also) -> bool:
    """One conditional update: True only for the caller that actually changed the row."""
    return bool(Payment.objects.filter(pk=payment_id, status__in=from_statuses, **also)
                .update(status=to_status, updated_at=timezone.now()))


# --- Closing payments that must no longer be paid ----------------------------


def expire_open(trip_request, *, keep_quote=None) -> int:
    """
    Close every open payment of this request (except those of `keep_quote`):
    for a newer price, a cancellation or a decline. Always closed in our
    table, at once; closed at Stripe after the surrounding change is saved,
    as well as that can be done. If Stripe can't be reached the page closes
    by itself within the hour, and a payment made on it meanwhile is
    recorded as "needs attention" rather than accepted.

    Safe on any path: with the payment tables missing it does nothing.
    """
    rows = Payment.objects.filter(quote__trip_request_id=trip_request.pk, status=PaymentStatus.OPEN)
    if keep_quote is not None:
        rows = rows.exclude(quote=keep_quote)
    found = payments.safe_read(lambda: list(rows.values_list("pk", "session_id")), [])
    if not found:
        return 0
    Payment.objects.filter(pk__in=[pk for pk, _ in found], status=PaymentStatus.OPEN).update(
        status=PaymentStatus.EXPIRED, updated_at=timezone.now(),
    )
    sessions = [session_id for _, session_id in found if session_id]
    if sessions:
        transaction.on_commit(lambda: expire_at_stripe(sessions))
    return len(found)


def expire_at_stripe(session_ids) -> None:
    """Ask Stripe to close these pages. Best effort: never raises."""
    if not session_ids:
        return
    try:
        provider = payments.get_provider()
    except PaymentError:
        return
    for session_id in session_ids:
        try:
            provider.expire_session(session_id)
        except PaymentError:
            # Already closed, already paid, or Stripe unreachable. What Stripe says next is applied as usual.
            continue
        except Exception as exc:  # noqa: BLE001 - closing a page is never worth failing the caller
            logger.warning("Stripe call failed: step=expire_session error=%s", type(exc).__name__)


# --- Recording what Stripe says about a payment ------------------------------


def apply_session(state, *, failed: bool = False, actor=None, recheck: bool = False) -> str:
    """
    Record what Stripe answered about a session. The webhook, the return
    page, the daily check and the admin's "Check with Stripe" button all
    end here, and only here is a payment marked paid.

    Returns the payment's status afterwards, or UNKNOWN for a session this
    system did not open (which is ignored). `failed` is the webhook saying
    a delayed payment did not go through. `recheck` (the admin button only)
    lets a payment that needed attention be accepted when every check now
    passes, for example after a request cancelled by mistake is reopened.
    """
    found = Payment.objects.filter(session_id=state.id).values_list("pk", "quote__trip_request_id").first()
    if found is None:
        return UNKNOWN
    payment_id, request_id = found
    with transaction.atomic():
        trip_request = _lock_request(request_id)
        payment = Payment.objects.select_related("quote").get(pk=payment_id)
        status = _apply(payment, trip_request, state, failed=failed, actor=actor, recheck=recheck)
        if status in MONEY_ARRIVED:
            # Every delivery, first or repeat, sends whatever email is still owed.
            transaction.on_commit(lambda: send_pending(payment_id))
    return status


def _apply(payment, trip_request, state, *, failed, actor, recheck) -> str:
    now = timezone.now()
    if state.payment_status == "paid":
        if payment.status == PaymentStatus.PAID:
            return payment.status
        if payment.status == PaymentStatus.NEEDS_ATTENTION and not recheck:
            return payment.status
        problems = _problems(payment, trip_request, state)
        source = "admin" if actor is not None else "stripe"
        facts = {"quote": str(payment.quote_id), "request": str(trip_request.pk), "session": state.id,
                 "payment_intent": state.payment_intent, "asked_cents": payment.amount_cents,
                 "paid_cents": state.amount_total}
        money = {"paid_cents": state.amount_total, "payment_intent_id": state.payment_intent,
                 "livemode": state.livemode, "updated_at": now}
        if not problems:
            accept_from = [*NOT_FINAL, *([PaymentStatus.NEEDS_ATTENTION] if recheck else [])]
            changed = Payment.objects.filter(pk=payment.pk, status__in=accept_from).update(
                status=PaymentStatus.PAID, attention_reason="", **money,
                **({} if payment.paid_at else {"paid_at": now}),
            )
            if changed:
                record_change(actor=actor, action="payment_paid", resource_type="Payment",
                              resource_id=payment.pk, after=facts, source=source)
                # Nothing else of this request may be paid now.
                expire_open(trip_request)
                return PaymentStatus.PAID
        elif payment.status != PaymentStatus.NEEDS_ATTENTION:
            reason = " ".join(problems)
            changed = Payment.objects.filter(pk=payment.pk, status__in=NOT_FINAL).update(
                status=PaymentStatus.NEEDS_ATTENTION, attention_reason=reason, paid_at=now, **money,
            )
            if changed:
                record_change(actor=actor, action="payment_needs_attention", resource_type="Payment",
                              resource_id=payment.pk, after={**facts, "why": problems}, source=source)
                return PaymentStatus.NEEDS_ATTENTION
    elif state.status == "expired":
        _set_status(payment.pk, [PaymentStatus.OPEN], PaymentStatus.EXPIRED)
    elif failed and state.status == "complete" and state.payment_status == "unpaid":
        _set_status(payment.pk, [PaymentStatus.OPEN], PaymentStatus.FAILED)
    # Whatever the row says now: another delivery may have changed it first.
    return Payment.objects.values_list("status", flat=True).get(pk=payment.pk)


def _problems(payment, trip_request, state) -> list[str]:
    """
    Why a payment Stripe reports as paid can't simply be accepted. Empty
    when everything fits. Each sentence is for staff, on the payment's page:
    amounts are fine there, and nothing about the rider is said.
    """
    problems = []
    if state.mode != "payment":
        problems.append("Stripe reports this as something other than a one-time payment.")
    if state.amount_total != payment.amount_cents:
        taken = "an unknown amount" if state.amount_total is None else f"${state.amount_total / 100:,.2f}"
        problems.append(f"Stripe took {taken} but the price was ${payment.amount:,.2f}.")
    if state.currency != payments.CURRENCY:
        problems.append("The payment was not in US dollars.")
    try:
        expected_live = payments.get_provider().livemode
    except PaymentError:
        expected_live = None
    if expected_live is None or state.livemode != expected_live:
        problems.append("Stripe reports a test payment where a real one was expected, or the other way round.")
    if state.metadata_quote != str(payment.quote_id):
        problems.append("Stripe's record of this payment names a different price.")
    if state.payment_intent and Payment.objects.filter(payment_intent_id=state.payment_intent).exclude(
            pk=payment.pk).exists():
        problems.append("Stripe reports the same payment as another one already recorded.")
    newest = newest_delivered(trip_request.pk)
    if newest is None or newest.pk != payment.quote_id:
        problems.append("This price was replaced by a newer one before the payment arrived.")
    if trip_request.status in CLOSED:
        problems.append(f"The request was {trip_request.get_status_display().lower()} before the payment arrived.")
    if payments.holding(Payment.objects.filter(quote__trip_request_id=trip_request.pk)).exclude(
            pk=payment.pk).exists():
        problems.append("This ride was already paid online: this is a second payment.")
    payer = pricing.quote_payer(trip_request)
    if payer is not None and payer.payer_type in pricing.PROGRAM_PAYER_TYPES:
        problems.append("This rider's trips are paid by a Medicaid or broker payer: the customer should not pay.")
    return problems


# --- Emails about a payment --------------------------------------------------


def _claim(payment_id, stamp: str) -> bool:
    """Take the right to send one email: only one caller gets it, so a repeat delivery never sends it twice."""
    return bool(Payment.objects.filter(pk=payment_id, **{f"{stamp}__isnull": True}).update(**{stamp: timezone.now()}))


def _send_once(payment_id, stamp: str, send) -> None:
    if not _claim(payment_id, stamp):
        return
    try:
        sent = bool(send())
    except Exception as exc:  # noqa: BLE001 - an email is never worth failing a payment over
        logger.error("Payment email failed: stamp=%s error=%s", stamp, type(exc).__name__)
        sent = False
    if not sent:
        # Give the right back: the next delivery, or the daily check, sends it.
        Payment.objects.filter(pk=payment_id).update(**{stamp: None})


def send_pending(payment_id, notifier: NotificationService | None = None) -> None:
    """
    Send the emails a payment still owes, after its change is saved. Paid:
    the office is told to schedule the ride (no amount), and the customer
    gets "We received your payment". Needs attention: the office only.
    Never raises: the payment is recorded whatever happens to an email.
    """
    try:
        payment = (Payment.objects.select_related("quote__trip_request")
                   .filter(pk=payment_id, status__in=MONEY_ARRIVED).first())
        if payment is None:
            return
        notifier = notifier or NotificationService()
        trip_request = payment.quote.trip_request
        if payment.status == PaymentStatus.PAID:
            _send_once(payment.pk, "office_notified_at", lambda: notifier.notify_payment_received(trip_request))
            terms = payments.terms()
            _send_once(payment.pk, "customer_notified_at", lambda: notifier.send_payment_received(
                payment.quote, amount=f"${payment.paid:,.2f}", what_paying_means=terms.what_paying_means,
                refund_policy=terms.refund_policy, waiting=waiting_rule(trip_request),
            ))
        else:
            _send_once(payment.pk, "office_notified_at",
                       lambda: notifier.notify_payment_attention(trip_request, payment))
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.error("Payment emails failed: error=%s", type(exc).__name__)


# --- Refunds and disputes (made in Stripe, recorded here) ---------------------


def _by_intent(payment_intent_id) -> Payment | None:
    if not isinstance(payment_intent_id, str) or not payment_intent_id or len(payment_intent_id) > 255:
        return None
    return (Payment.objects.select_related("quote__trip_request")
            .filter(payment_intent_id=payment_intent_id, status__in=MONEY_ARRIVED).first())


def record_refund(payment_intent_id, amount_refunded) -> bool:
    """
    Stripe reports money refunded on a payment: keep the total refunded so
    far and when, audit it, and tell the office. `amount_refunded` is the
    running total in cents, so a repeat or an older report changes nothing.
    True when the record changed.
    """
    payment = _by_intent(payment_intent_id)
    if payment is None or isinstance(amount_refunded, bool) or not isinstance(amount_refunded, int):
        return False
    total = min(amount_refunded, payment.paid_cents) if payment.paid_cents else amount_refunded
    if total <= 0:
        return False
    with transaction.atomic():
        now = timezone.now()
        changed = Payment.objects.filter(pk=payment.pk, refunded_cents__lt=total).update(
            refunded_cents=total, refunded_at=now, updated_at=now,
        )
        if not changed:
            return False
        record_change(actor=None, action="payment_refunded", resource_type="Payment", resource_id=payment.pk,
                      after={"payment_intent": payment_intent_id, "refunded_cents": total,
                             "paid_cents": payment.paid_cents}, source="stripe")
        trip_request = payment.quote.trip_request
        transaction.on_commit(lambda: _tell_office(lambda n: n.notify_payment_refunded(trip_request, payment)))
    return True


def record_dispute(payment_intent_id) -> bool:
    """Stripe reports that the cardholder disputed the charge: keep the date, audit it, tell the office."""
    payment = _by_intent(payment_intent_id)
    if payment is None:
        return False
    with transaction.atomic():
        now = timezone.now()
        changed = Payment.objects.filter(pk=payment.pk, disputed_at__isnull=True).update(
            disputed_at=now, updated_at=now,
        )
        if not changed:
            return False
        record_change(actor=None, action="payment_disputed", resource_type="Payment", resource_id=payment.pk,
                      after={"payment_intent": payment_intent_id}, source="stripe")
        trip_request = payment.quote.trip_request
        transaction.on_commit(lambda: _tell_office(lambda n: n.notify_payment_disputed(trip_request, payment)))
    return True


def _tell_office(send) -> None:
    try:
        send(NotificationService())
    except Exception as exc:  # noqa: BLE001 - the record is saved; an email never undoes it
        logger.error("Payment email failed: error=%s", type(exc).__name__)


# --- The daily check -----------------------------------------------------------


def daily_check(now=None) -> dict | str:
    """
    Ask Stripe again about every recent payment that isn't settled, so a
    payment whose webhook never arrived is still recorded. "Recent" is the
    last three days; "not settled" is still open, or closed by us before
    Stripe's own closing time (a cancellation or a newer price, where
    Stripe may not have heard). Also sends any email a recorded payment
    still owes. Does nothing, and asks Stripe nothing, while online payment
    isn't set up on the server.
    """
    if not payments.configured():
        return "off"
    now = now or timezone.now()
    since = now - timedelta(days=RECHECK_DAYS)
    rows = payments.safe_read(lambda: list(
        Payment.objects.filter(created_at__gte=since)
        .filter(Q(status=PaymentStatus.OPEN) | Q(status=PaymentStatus.EXPIRED, updated_at__lt=F("expires_at")))
        .order_by("created_at")[:RECHECK_LIMIT]
    ), None)
    if rows is None:
        return "off"
    try:
        provider = payments.get_provider()
    except PaymentError:
        return "off"
    result = {"checked": 0, "paid": 0, "needs_attention": 0, "unreachable": 0}
    started, misses = time.monotonic(), 0
    for row in rows:
        if not row.session_id:
            # Never opened at Stripe: nothing to ask, it just closes at its time.
            if row.status == PaymentStatus.OPEN and row.expires_at and row.expires_at <= now:
                _set_status(row.pk, [PaymentStatus.OPEN], PaymentStatus.EXPIRED)
            continue
        if misses >= 2 or time.monotonic() - started > RECHECK_SECONDS:
            result["unreachable"] += 1  # left for tomorrow's run
            continue
        try:
            state = provider.retrieve_session(row.session_id)
        except PaymentError:
            misses += 1
            result["unreachable"] += 1
            continue
        misses = 0
        result["checked"] += 1
        status = apply_session(state)
        if status in MONEY_ARRIVED and row.status not in MONEY_ARRIVED:
            result[status] += 1
    owed = payments.safe_read(lambda: list(
        Payment.objects.filter(created_at__gte=since, status__in=MONEY_ARRIVED)
        .filter(Q(office_notified_at__isnull=True) | Q(status=PaymentStatus.PAID, customer_notified_at__isnull=True))
        .values_list("pk", flat=True)[:RECHECK_LIMIT]
    ), [])
    for payment_id in owed:
        send_pending(payment_id)
    return result


# --- The webhook's signature ---------------------------------------------------

SIGNATURE_TOLERANCE = 300  # seconds, either way


def verify_signature(body: bytes, header: str, secrets, *, now: float | None = None,
                     tolerance: int = SIGNATURE_TOLERANCE) -> bool:
    """
    Did Stripe sign exactly these bytes, just now? The header holds
    comma-separated `key=value` entries: `t` (when it was signed) and one
    or more `v1` signatures. The signed text is "<t>.<the raw body>", with
    HMAC-SHA256 under the endpoint's secret. Accepted when ANY `v1` matches
    ANY configured secret (Stripe sends several while a secret is being
    rolled) and `t` is within five minutes of now, either way. `v0` and
    unknown entries are ignored.
    """
    if not isinstance(body, (bytes, bytearray)) or not isinstance(header, str) or not header or not secrets:
        return False
    stamp, candidates = None, []
    for entry in header.split(","):
        key, found, value = entry.strip().partition("=")  # on the first "=" only
        if not found:
            continue
        if key == "t" and stamp is None:
            stamp = value
        elif key == "v1":
            candidates.append(value)
    if stamp is None or not candidates or not (stamp.isascii() and stamp.isdigit()) or len(stamp) > 12:
        return False
    if abs((time.time() if now is None else now) - int(stamp)) > tolerance:
        return False
    signed = stamp.encode("ascii") + b"." + bytes(body)
    matched = False
    for secret in secrets:
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest().encode("ascii")
        for candidate in candidates:
            if hmac.compare_digest(expected, candidate.encode("utf-8")):
                matched = True
    return matched

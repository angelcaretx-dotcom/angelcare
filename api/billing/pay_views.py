"""
The public pages of online payment, on this server's own address (ADR
0050): the page the price email's link opens, the page Stripe sends the
payer back to, and the address Stripe reports to (the webhook).

Plain Django views, not the REST framework: no throttle and no parser may
stand between Stripe and the webhook, whose signature is checked over the
exact bytes received.

Nothing here shows or logs a name, an address, a date, a key, a token or
anything Stripe sent: the pages show the reference, the total and the
office's two texts, and a log line holds the step and the kind of error.
"""

import json
import logging
import re
from functools import wraps

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect, JsonResponse, UnreadablePostError
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import checkout, payments
from .models import Payment, PaymentEvent
from .payments import PaymentError

logger = logging.getLogger(__name__)

GENERIC = "This payment link can't be used. Please call us."
UNAVAILABLE = "Online payment is not available right now. Please call us."
CONFIRMING = (
    "Thank you. We are confirming your payment with the card processor. "
    "We will email you when it is confirmed. If you do not hear from us within an hour, please call us."
)

SESSION_EVENTS = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
}
_EVENT_ID = re.compile(r"evt_[A-Za-z0-9_]{1,250}")
_EVENT_TYPE = re.compile(r"[a-z0-9_.]{1,100}")


class SeeOther(HttpResponseRedirect):
    """A redirect that turns the POST of the button into a GET of Stripe's page."""

    status_code = 303


def private_page(view):
    """Every answer of the pay pages: never stored, never indexed, and its address never passed on."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        response["X-Robots-Tag"] = "noindex"
        return response

    return wrapped


def _page(request, *, status=200, **context):
    return render(request, "billing/pay.html", context, status=status)


def _refused(request, reason: str = "", *, status=200):
    return _page(request, status=status, state="refused", message=GENERIC, reason=reason)


# The view checks the CSRF token itself (csrf_protect), inside `private_page`, so that even a
# refused POST carries the three headers; csrf_exempt only tells the site-wide check to stand aside.
@csrf_exempt
@private_page
@require_http_methods(["GET", "POST"])
@csrf_protect
def pay(request, token):
    """
    The page behind the price email's link. Opening it (GET) only shows
    the price and the terms: it changes nothing and never contacts Stripe,
    because mail scanners open links too. Pressing the button (POST) sends
    the customer to Stripe's page.
    """
    quote_id = payments.read_pay_token(token)
    if quote_id is None:
        return _refused(request, status=404)
    try:
        if request.method == "GET":
            offer = checkout.look(quote_id)
            if offer.refusal is not None:
                return _refused(request, offer.refusal)
            return _page(request, state="offer", offer=offer)
        started = checkout.start(quote_id)
    except Exception as exc:  # noqa: BLE001 - the customer gets a plain page, never an error page with details
        logger.error("Pay page failed: step=%s error=%s", request.method.lower(), type(exc).__name__)
        return _page(request, status=503, state="unavailable", message=UNAVAILABLE)
    if started.url:
        return SeeOther(started.url)
    if started.unavailable:
        return _page(request, status=503, state="unavailable", message=UNAVAILABLE)
    return _refused(request, started.refusal or "")


@private_page
@require_GET
def pay_return(request):
    """
    Where Stripe sends the payer afterwards. Asks Stripe about the session
    (the address alone proves nothing), records the answer the same way the
    webhook does, and sends the payer on to the website's "received" or
    "cancelled" page.
    """
    received = HttpResponseRedirect(f"{settings.WEBSITE_URL}/payment/received")
    cancelled = HttpResponseRedirect(f"{settings.WEBSITE_URL}/payment/cancelled")
    session_id = request.GET.get("session_id", "")
    try:
        if not _ours(session_id):
            return cancelled
    except Exception as exc:  # noqa: BLE001 - never an error page
        logger.error("Pay return failed: step=lookup error=%s", type(exc).__name__)
        return cancelled
    try:
        state = payments.get_provider().retrieve_session(session_id)
        status = checkout.apply_session(state)
    except Exception as exc:  # noqa: BLE001 - a payment of ours that can't be confirmed right now
        if not isinstance(exc, PaymentError):
            logger.error("Pay return failed: step=apply error=%s", type(exc).__name__)
        # Not "received" (unproven) and not "no payment was taken" (probably untrue): the webhook
        # or the daily check records it, and the customer is told what to expect.
        return _page(request, state="confirming", message=CONFIRMING)
    return received if status in checkout.MONEY_ARRIVED else cancelled


def _ours(session_id) -> bool:
    """Is this the id of a session this system opened? Anything else is ignored without asking Stripe."""
    try:
        payments.PaymentProvider.check_session_id(session_id, "lookup")
    except PaymentError:
        return False
    return Payment.objects.filter(session_id=session_id).exists()


def _answer(detail: str, status: int = 200) -> JsonResponse:
    return JsonResponse({"detail": detail}, status=status)


def _record(event_id: str, event_type: str) -> bool:
    """Remember a handled event. False when it was already there: another delivery of it got here first."""
    try:
        with transaction.atomic():
            PaymentEvent.objects.create(event_id=event_id, event_type=event_type)
        return True
    except IntegrityError:
        return False


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Stripe's reports. The signature is checked over the raw bytes before
    anything is read from them. A report is then only a hint: for a
    payment, Stripe is asked about the session and that answer is what gets
    recorded. Each event is handled once; a repeat answers 200 and does
    nothing. 503 only when Stripe can't be asked, so that Stripe sends the
    report again.
    """
    secrets = payments.webhook_secrets()
    if not secrets:
        return _answer("Not configured.", 503)
    try:
        body = request.body
    except (RequestDataTooBig, UnreadablePostError):
        return _answer("Bad request.", 400)
    if not checkout.verify_signature(body, request.headers.get("Stripe-Signature", ""), secrets):
        logger.warning("Stripe webhook refused: step=signature")
        return _answer("Bad signature.", 400)
    try:
        event = json.loads(body)
    except ValueError:  # includes bytes that aren't UTF-8
        return _answer("Bad request.", 400)
    event_id = event.get("id") if isinstance(event, dict) else None
    event_type = event.get("type") if isinstance(event, dict) else None
    if not (isinstance(event_id, str) and _EVENT_ID.fullmatch(event_id)
            and isinstance(event_type, str) and _EVENT_TYPE.fullmatch(event_type)):
        return _answer("Bad request.", 400)
    if PaymentEvent.objects.filter(event_id=event_id).exists():
        return _answer("Already handled.")

    try:
        provider = payments.get_provider()
    except PaymentError:
        return _answer("Not configured.", 503)
    data = event.get("data")
    thing = data.get("object") if isinstance(data, dict) else None
    thing = thing if isinstance(thing, dict) else {}
    if (event.get("livemode") is True) != provider.livemode:
        # A test report on a server that takes real payments, or the other way round: never applied.
        logger.warning("Stripe webhook ignored: step=livemode type=%s", event_type)
        _record(event_id, event_type)
        return _answer("Ignored.")

    if event_type in SESSION_EVENTS:
        session_id = thing.get("id")
        if not _ours(session_id):
            _record(event_id, event_type)
            return _answer("Ignored.")
        try:
            state = provider.retrieve_session(session_id)
        except PaymentError as exc:
            if exc.http_status == 404:
                # Stripe doesn't know this session under this key: there is nothing to apply, now or later.
                _record(event_id, event_type)
                return _answer("Ignored.")
            return _answer("Try again later.", 503)
        with transaction.atomic():
            if not _record(event_id, event_type):
                return _answer("Already handled.")
            checkout.apply_session(state, failed=event_type == "checkout.session.async_payment_failed")
        return _answer("Recorded.")

    with transaction.atomic():
        if not _record(event_id, event_type):
            return _answer("Already handled.")
        if event_type == "charge.refunded":
            checkout.record_refund(thing.get("payment_intent"), thing.get("amount_refunded"))
        elif event_type == "charge.dispute.created":
            checkout.record_dispute(thing.get("payment_intent"))
    return _answer("Recorded.")

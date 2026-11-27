"""
Emailing a ride's price to the person who asked for it (ADR 0047, 0049).

Staff work the price out on the request's "Email the price" page; this
saves what was sent as a Quote, emails the price with each part listed,
and moves the request on to Contacted. Nobody phones the customer to
tell them a price.

Everything the customer reads is worded here from what kind of charge a
line is, never from the office's label for it: that label names the
service level, which stays out of email (ADR 0027).
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import record_change
from notifications.services import NotificationService
from transportation.models import ReturnRide, ServiceType, TripRequest, TripRequestStatus

from . import checkout, payments, pricing
from .models import Quote

# A request that hasn't been answered yet becomes Contacted once its price is emailed.
UNANSWERED = (TripRequestStatus.NEW, TripRequestStatus.REVIEWED)
# A ride that isn't happening isn't priced.
CLOSED = (TripRequestStatus.CANCELLED, TripRequestStatus.DECLINED)
# The same price sent again this soon is a double click, not a second email.
DOUBLE_CLICK = timedelta(minutes=2)
# Shown when a price is sent without the two confirmations (ADR 0049).
NOT_CONFIRMED = (
    "Nothing was sent. Tick both boxes first: that you checked the miles on the map, "
    "and that the customer pays for this ride."
)
# A price that was paid online is final (ADR 0050): no new price until the payment is refunded.
ALREADY_PAID = "This ride is already paid online. Refund it in Stripe first if the price has to change."


def covers(trip_request) -> str:
    """What the price is for, in the customer's words. No addresses, times or health details (ADR 0027)."""
    one_way = trip_request.return_ride == ReturnRide.NONE
    if one_way and trip_request.service_type == ServiceType.EQUIPMENT_DELIVERY:
        return "One-way trip"  # not "Delivery": the email never names the service
    # A delivery that staff gave a ride back is priced as a round trip, so it is called one:
    # this line must agree with the rows above it. Neither wording names the service.
    if one_way:
        return "One-way ride"
    if trip_request.return_ride == ReturnRide.WAIT:
        return "Round trip, with the driver waiting for you"
    return "Round trip (there and back)"


def wait_note(trip_request, card, wait_minutes: int) -> str:
    """For a wait-and-return ride: the free minutes, what the price covers, and the rate after that."""
    covered = pricing.covered_wait_minutes(trip_request, card, wait_minutes)
    if covered is None:
        return ""
    block = "minute" if card.wait_block_minutes <= 1 else f"{card.wait_block_minutes} minutes"
    rate = card.wait_rate_for(trip_request.service_type)
    free = f"The first {card.wait_free_minutes} minutes of waiting are free. " if card.wait_free_minutes else ""
    return (
        f"{free}This price covers up to {covered} minutes of waiting. "
        f"A longer wait is billed at ${rate} per {block} after the ride."
    )


def _dollars(value) -> str:
    return f"${Decimal(value):,.2f}"


def _number(value) -> str:
    """12.40 -> 12.4 and 5.00 -> 5: a count the way a person writes it."""
    text = f"{Decimal(value):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# How a charge line names its ride, by its `leg`: (after a fee, in front of the miles).
_RIDE = {"there": (", ride there", "Miles there"), "back": (", ride back", "Miles back")}
_FEES = {"base": "Booking fee", "loading": "Loading fee", "after_hours": "After-hours fee"}


def _both_ways(lines):
    """
    The two miles lines of a round trip, (there, back), when the email can
    show them as one row: the same price per mile, and the miles added
    together times that price is exactly the two amounts added together.
    Otherwise None, and each ride keeps its own row: a row is never printed
    whose numbers don't multiply out.
    """
    miles = [line for line in lines if line.kind == "miles"]
    if len(miles) != 2 or {line.leg for line in miles} != {"there", "back"}:
        return None
    there, back = sorted(miles, key=lambda line: line.leg == "back")
    if there.unit != back.unit:
        return None
    together = pricing.total([pricing.Line("", there.quantity + back.quantity, there.unit)])
    return (there, back) if together == there.amount + back.amount else None


def _miles_row(name: str, charged, unit, driven, included) -> str:
    """
    A miles row. On a card with included miles it says the real distance
    first, so the miles charged don't read as the length of the ride:
    "Miles: 12.4 miles, first 5 included: 7.4 x $2.00". The distance is
    only printed when it and the included miles give exactly the miles
    charged.
    """
    charge = f"{_number(charged)} x {_dollars(unit)}"
    if included and driven is not None and Decimal(driven) - Decimal(included) == charged:
        return f"{name}: {_number(driven)} miles, first {_number(included)} included: {charge}"
    return f"{name}: {charge}"


def customer_rows(trip_request, card, lines, wait_minutes, miles=None) -> list[tuple[str, str]]:
    """
    The breakdown the customer reads: (what it is, amount) rows, in the
    order of the charge lines (ADR 0049). The amounts add up to the total.

    Each row is worded from the line's kind, leg, quantity, unit and amount
    only. The card adds two facts a line doesn't carry: the miles it
    includes and the length of a wait block; `miles` is the one-way
    distance the price was worked out from. Nothing is taken from the
    request, and the minutes typed are not printed: the wait row shows what
    is charged, and `wait_note` says what the price covers.

    A round trip's miles are one row for both ways when they can be (see
    `_both_ways`), placed after the booking and loading fees. A discount
    (ADR 0051) is the last row, with its percentage when it was one. Its
    reason is never printed.
    """
    rows = []
    both_ways = _both_ways(lines)
    after_fees = None  # where the one row for the miles both ways goes: after the booking and loading fees
    for line in lines:
        after_fee, miles_name = _RIDE.get(line.leg, ("", "Miles"))
        if line.kind in _FEES:
            what = _FEES[line.kind] + after_fee
        elif line.kind == "miles" and both_ways:
            if after_fees is None:
                after_fees = len(rows)
            continue  # one row for both rides, placed below
        elif line.kind == "miles":
            what = _miles_row(miles_name, line.quantity, line.unit, miles, card.included_miles)
        elif line.kind == "discount":
            percent = line.extra.get("percent")
            rows.append((f"Discount ({percent}%)" if percent else "Discount", f"-{_dollars(-line.amount)}"))
            continue
        elif line.kind == "wait":
            if card.wait_block_minutes <= 1:
                minutes = "minute" if line.quantity == 1 else "minutes"
                what = f"Waiting time: {_number(line.quantity)} {minutes} at {_dollars(line.unit)}"
            else:
                what = (f"Waiting time: {_number(line.quantity)} x {card.wait_block_minutes} minutes "
                        f"at {_dollars(line.unit)}")
        else:
            # A kind this email has no words for. Its label is never printed, and
            # leaving it out would make the rows add up to less than the total.
            what = "Other charge"
        rows.append((what, _dollars(line.amount)))
        if both_ways and line.kind in ("base", "loading"):
            after_fees = len(rows)
    if both_ways:
        there, back = both_ways
        what = _miles_row("Miles, round trip", there.quantity + back.quantity, there.unit,
                          None if miles is None else miles * 2, card.included_miles * 2)
        rows.insert(after_fees, (what, _dollars(there.amount + back.amount)))
    return rows


def how_it_works(trip_request, lines) -> str:
    """
    "How your price works": two or three short sentences, only about the
    parts this price has. It promises nothing: each sentence says what a
    charge is, or how many times it is charged.
    """
    kinds = {line.kind for line in lines}
    sentences = []
    if "base" in kinds:
        sentences.append("The booking fee covers scheduling the trip and sending a vehicle.")
    if "miles" in kinds:
        sentences.append("Miles are the driving miles between your two addresses.")
    if trip_request.return_ride != ReturnRide.NONE and "base" in kinds:
        fees = "the booking fee and the loading fee" if "loading" in kinds else "the booking fee"
        if any(line.kind == "base" and line.leg for line in lines):
            # The card charges the fees on each ride.
            miles = ", and its own miles" if "miles" in kinds else ""
            sentences.append(f"A round trip is two rides: each ride has {fees}{miles}.")
        else:
            verb = "are" if "loading" in kinds else "is"
            miles = ", and the miles both ways" if "miles" in kinds else ""
            sentences.append(f"For a round trip {fees} {verb} charged once{miles}.")
    return " ".join(sentences)


def no_wait_note(trip_request) -> str:
    """
    For a round trip where the driver doesn't wait (a ride back at a set
    time, or when the rider calls): what that means, and how to ask for a
    waiting driver instead. Facts only (ADR 0049): it doesn't say which
    costs less, how long a wait would be, or that the same driver comes
    back. None of those is something the system can promise.
    """
    if trip_request.return_ride not in (ReturnRide.SCHEDULED, ReturnRide.WILL_CALL):
        return ""
    if trip_request.service_type == ServiceType.EQUIPMENT_DELIVERY:
        return ""  # nobody rides, so there is nobody to be kept waiting
    # Said once: "How your price works" already says a round trip is two rides.
    sentences = ["On this booking the driver does not wait for you: the ride back is a separate trip."]
    if trip_request.return_ride == ReturnRide.WILL_CALL:
        # Only a rider who calls for the ride back can be kept waiting by it.
        sentences.append("When you call for the ride back, there can be a wait if the driver is finishing another ride.")
    sentences.append(
        "If you would rather have the driver wait and bring you straight back, "
        "tell us and we will send you a new price."
    )
    return " ".join(sentences)


def send(trip_request, *, miles, wait_minutes, user, miles_checked: bool = False, customer_pays: bool = False,
         discount: pricing.Discount | None = None, notifier: NotificationService | None = None) -> Quote:
    """
    Price the request, save the quote and email it. Raises PricingError
    when there's no price to send. A failed email keeps the quote, marked
    not delivered, and leaves the request's status alone.

    Nothing is sent unless the sender confirmed both things (ADR 0049):
    the miles were checked on the map (they start as an estimate), and the
    customer is the one paying (a Medicaid rider, or a facility that is
    invoiced, must never be emailed a price).

    A `discount` (ADR 0051) is checked here as well as on the price page.
    It is saved as the last entry of the quote's lines, so `amount` is the
    price after the discount, and it is in the audit row with its reason.
    The customer's email shows the discount, never the reason.

    The same price sent again within two minutes is a double click: nothing
    is sent, and the quote of the first press is returned with
    `already_sent` set to True. On every other quote it is False.

    Online payment (ADR 0050): while it is on, the email carries a pay
    link; a delivered price closes the payment page of any older one; and
    a ride that is already paid online gets no new price until that
    payment is refunded in Stripe.
    """
    wait_minutes = wait_minutes or 0
    if trip_request.status in CLOSED:
        raise pricing.PricingError(f"This request is {trip_request.get_status_display().lower()}: there is no price to email.")
    if not (trip_request.email or "").strip():
        raise pricing.PricingError("This request has no email address: add one before emailing the price.")
    if not (miles_checked is True and customer_pays is True):
        raise pricing.PricingError(NOT_CONFIRMED)
    card, lines, _ = pricing.quote(trip_request, miles=miles, wait_minutes=wait_minutes, discount=discount)
    # The price after any discount: what the customer is told, and what a pay link asks for.
    amount = pricing.total(lines)
    taken_off = next((line for line in lines if line.kind == "discount"), None)
    # The payment settings, while online payment is on and this total can be paid by card (ADR 0050);
    # otherwise None, and the email is exactly what it was before. Read before anything is saved.
    pay_terms = checkout.pay_terms(amount)
    with transaction.atomic():
        # A payment for this request may be arriving at this very moment: the request row is locked
        # first, as the payment code does, so "is it paid?" is asked after that payment is recorded.
        locked = TripRequest.objects.select_for_update().filter(pk=trip_request.pk).first()
        if locked is not None and locked.status in CLOSED:
            raise pricing.PricingError(f"This request is {locked.get_status_display().lower()}: there is no price to email.")
        if payments.request_paid(trip_request):
            raise pricing.PricingError(ALREADY_PAID)
        just_sent = trip_request.quotes.filter(
            delivered=True, amount=amount, miles=miles, wait_minutes=wait_minutes, sent_to=trip_request.email,
            created_at__gte=timezone.now() - DOUBLE_CLICK,
        ).first()
        if just_sent is not None:
            just_sent.already_sent = True
            return just_sent
        quote = Quote.objects.create(
            trip_request=trip_request, rate_card=card, miles=miles, wait_minutes=wait_minutes,
            lines=[line.as_dict() for line in lines], amount=amount, sent_to=trip_request.email, sent_by=user,
        )
        quote.already_sent = False
        record_change(
            actor=user, action="quote_sent", resource_type="TripRequest", resource_id=trip_request.pk,
            after={"quote": str(quote.pk), "amount": str(amount), "rate_card": str(card.pk),
                   "miles": None if miles is None else str(miles), "wait_minutes": wait_minutes,
                   # What the sender confirmed before this price went out (ADR 0049).
                   "miles_checked": True, "who_pays": "customer",
                   # A discount is money given away: what was typed, what it came to, and why (ADR 0051).
                   **({"discount": {
                       "type": "percent" if discount.kind == pricing.DISCOUNT_PERCENT else "dollars",
                       "value": str(discount.value), "amount": str(-taken_off.amount),
                       "reason": taken_off.extra["reason"],
                   }} if taken_off is not None else {})},
            source="admin",
        )

    pay = checkout.pay_context(quote, pay_terms)
    delivered = (notifier or NotificationService()).send_quote(
        quote, covers=covers(trip_request),
        rows=customer_rows(trip_request, card, lines, wait_minutes, miles),
        how_it_works=how_it_works(trip_request, lines),
        wait_note=wait_note(trip_request, card, wait_minutes),
        no_wait_note=no_wait_note(trip_request),
        # Only while online payment is on: the pay link and the office's texts about paying and refunds.
        **({"pay": pay} if pay else {}),
    )
    if not delivered:
        quote.delivered = False
        quote.save(update_fields=["delivered"])
        return quote

    # The customer now holds a newer price: a payment page still open for an older one is closed
    # (ADR 0050), in our table at once and at Stripe as well as that can be done.
    checkout.expire_open(trip_request, keep_quote=quote)

    if trip_request.status in UNANSWERED:
        previous = trip_request.status
        trip_request.status = TripRequestStatus.CONTACTED
        trip_request.save(update_fields=["status", "updated_at"])
        record_change(
            actor=user, action="status_changed", resource_type="TripRequest", resource_id=trip_request.pk,
            before={"status": previous}, after={"status": TripRequestStatus.CONTACTED}, source="admin",
        )
    return quote

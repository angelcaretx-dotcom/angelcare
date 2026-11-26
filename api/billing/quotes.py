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

from django.utils import timezone

from audit.services import record_change
from notifications.services import NotificationService
from transportation.models import ReturnRide, ServiceType, TripRequestStatus

from . import pricing
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


def customer_rows(trip_request, card, lines, wait_minutes) -> list[tuple[str, str]]:
    """
    The breakdown the customer reads: one (what it is, amount) row per
    charge line, in the order of the lines (ADR 0049).

    Each row is worded from the line's kind, leg, quantity, unit and amount
    only. The card adds two facts a line doesn't carry: the miles it
    includes and the length of a wait block. Nothing is taken from the
    request, and the minutes typed are not printed: the wait row shows what
    is charged, and `wait_note` says what the price covers.
    """
    rows = []
    for line in lines:
        after_fee, miles = _RIDE.get(line.leg, ("", "Miles"))
        if line.kind in _FEES:
            what = _FEES[line.kind] + after_fee
        elif line.kind == "miles":
            what = f"{miles}: {_number(line.quantity)} x {_dollars(line.unit)}"
            if card.included_miles:
                what += f" (after the first {_number(card.included_miles)} miles)"
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
    sentences = [
        "On this booking the driver does not wait for you. The driver leaves after dropping you off, "
        "and the ride back is a second trip."
    ]
    if trip_request.return_ride == ReturnRide.WILL_CALL:
        # Only a rider who calls for the ride back can be kept waiting by it.
        sentences.append("When you call for the ride back, there can be a wait if the driver is finishing another ride.")
    sentences.append(
        "If you would rather have the driver wait for you and bring you straight back, "
        "tell us and we will send you a new price."
    )
    return " ".join(sentences)


def send(trip_request, *, miles, wait_minutes, user, miles_checked: bool = False, customer_pays: bool = False,
         notifier: NotificationService | None = None) -> Quote:
    """
    Price the request, save the quote and email it. Raises PricingError
    when there's no price to send. A failed email keeps the quote, marked
    not delivered, and leaves the request's status alone.

    Nothing is sent unless the sender confirmed both things (ADR 0049):
    the miles were checked on the map (they start as an estimate), and the
    customer is the one paying (a Medicaid rider, or a facility that is
    invoiced, must never be emailed a price).
    """
    wait_minutes = wait_minutes or 0
    if trip_request.status in CLOSED:
        raise pricing.PricingError(f"This request is {trip_request.get_status_display().lower()}: there is no price to email.")
    if not (trip_request.email or "").strip():
        raise pricing.PricingError("This request has no email address: add one before emailing the price.")
    if not (miles_checked is True and customer_pays is True):
        raise pricing.PricingError(NOT_CONFIRMED)
    card, lines, _ = pricing.quote(trip_request, miles=miles, wait_minutes=wait_minutes)
    amount = pricing.total(lines)
    just_sent = trip_request.quotes.filter(
        delivered=True, amount=amount, miles=miles, wait_minutes=wait_minutes, sent_to=trip_request.email,
        created_at__gte=timezone.now() - DOUBLE_CLICK,
    ).first()
    if just_sent is not None:
        return just_sent
    quote = Quote.objects.create(
        trip_request=trip_request, rate_card=card, miles=miles, wait_minutes=wait_minutes,
        lines=[line.as_dict() for line in lines], amount=amount, sent_to=trip_request.email, sent_by=user,
    )
    record_change(
        actor=user, action="quote_sent", resource_type="TripRequest", resource_id=trip_request.pk,
        after={"quote": str(quote.pk), "amount": str(amount), "rate_card": str(card.pk),
               "miles": None if miles is None else str(miles), "wait_minutes": wait_minutes,
               # What the sender confirmed before this price went out (ADR 0049).
               "miles_checked": True, "who_pays": "customer"},
        source="admin",
    )

    delivered = (notifier or NotificationService()).send_quote(
        quote, covers=covers(trip_request),
        rows=customer_rows(trip_request, card, lines, wait_minutes),
        how_it_works=how_it_works(trip_request, lines),
        wait_note=wait_note(trip_request, card, wait_minutes),
        no_wait_note=no_wait_note(trip_request),
    )
    if not delivered:
        quote.delivered = False
        quote.save(update_fields=["delivered"])
        return quote

    if trip_request.status in UNANSWERED:
        previous = trip_request.status
        trip_request.status = TripRequestStatus.CONTACTED
        trip_request.save(update_fields=["status", "updated_at"])
        record_change(
            actor=user, action="status_changed", resource_type="TripRequest", resource_id=trip_request.pk,
            before={"status": previous}, after={"status": TripRequestStatus.CONTACTED}, source="admin",
        )
    return quote

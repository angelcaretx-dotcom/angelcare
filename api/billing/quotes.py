"""
Emailing a ride's price to the person who asked for it (ADR 0047).

Staff work the price out on the request's "Email the price" page; this
saves what was sent as a Quote, emails the total, and moves the request
on to Contacted. Nobody phones the customer to tell them a price.
"""

from audit.services import record_change
from notifications.services import NotificationService
from transportation.models import ReturnRide, ServiceType, TripRequestStatus

from . import pricing
from .models import Quote

# A request that hasn't been answered yet becomes Contacted once its price is emailed.
UNANSWERED = (TripRequestStatus.NEW, TripRequestStatus.REVIEWED)


def covers(trip_request) -> str:
    """What the price is for, in the customer's words. No addresses, times or health details (ADR 0027)."""
    if trip_request.service_type == ServiceType.EQUIPMENT_DELIVERY:
        return "Delivery"
    if trip_request.return_ride == ReturnRide.NONE:
        return "One-way ride"
    if trip_request.return_ride == ReturnRide.WAIT:
        return "Round trip, with the driver waiting for you"
    return "Round trip (there and back)"


def wait_note(trip_request, card, wait_minutes: int) -> str:
    covered = pricing.covered_wait_minutes(trip_request, card, wait_minutes)
    if covered is None:
        return ""
    block = "minute" if card.wait_block_minutes <= 1 else f"{card.wait_block_minutes} minutes"
    rate = card.wait_rate_for(trip_request.service_type)
    return (
        f"This price covers up to {covered} minutes of waiting. "
        f"A longer wait is billed at ${rate} per {block} after the ride."
    )


def send(trip_request, *, miles, wait_minutes, user, notifier: NotificationService | None = None) -> Quote:
    """
    Price the request, save the quote and email it. Raises PricingError
    when there's no price to send. A failed email keeps the quote, marked
    not delivered, and leaves the request's status alone.
    """
    wait_minutes = wait_minutes or 0
    card, lines, _ = pricing.quote(trip_request, miles=miles, wait_minutes=wait_minutes)
    amount = pricing.total(lines)
    quote = Quote.objects.create(
        trip_request=trip_request, rate_card=card, miles=miles, wait_minutes=wait_minutes,
        lines=[line.as_dict() for line in lines], amount=amount, sent_to=trip_request.email, sent_by=user,
    )
    record_change(
        actor=user, action="quote_sent", resource_type="TripRequest", resource_id=trip_request.pk,
        after={"quote": str(quote.pk), "amount": str(amount), "rate_card": str(card.pk),
               "miles": None if miles is None else str(miles), "wait_minutes": wait_minutes},
        source="admin",
    )

    delivered = (notifier or NotificationService()).send_quote(
        quote, covers=covers(trip_request), wait_note=wait_note(trip_request, card, wait_minutes)
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

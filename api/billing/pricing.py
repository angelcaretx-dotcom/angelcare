"""
Charges and driver pay, worked out from rate cards, pay plans and each
trip's own record (ADR 0039). Nothing is typed: if a number the card
needs is missing (miles, a wait the driver app didn't record), the trip
isn't billed and staff are told what's missing.

Times are Texas time: which day's card applies, and whether a pickup is
after-hours, are judged on the Texas clock.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.services import record_change
from payers.models import PayerType
from transportation.models import ReturnRide
from trips.models import Trip, TripEventType, TripLeg, TripStatus
from vehicles.models import VehicleCheck

from .models import DriverPayPlan, Invoice, InvoiceStatus, RateCard

CENT = Decimal("0.01")
BILLED_STATUSES = (TripStatus.COMPLETED, TripStatus.NO_SHOW)
# Payers whose rates are set by the program or contract, never quoted to a rider (ADR 0047).
PROGRAM_PAYER_TYPES = (PayerType.MEDICAID_MCO, PayerType.BROKER)
# No real ride costs this much: a larger total means mistyped miles or minutes.
MAX_QUOTE = Decimal("20000.00")


class PricingError(Exception):
    pass


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def local(dt):
    return timezone.localtime(dt, _zone())


@dataclass
class Line:
    label: str
    quantity: Decimal
    unit: Decimal
    amount: Decimal = field(init=False)

    def __post_init__(self):
        self.amount = _money(self.quantity * self.unit)

    def as_dict(self) -> dict:
        return {"label": self.label, "quantity": str(self.quantity), "unit": str(self.unit), "amount": str(self.amount)}


def card_on(payer, day) -> RateCard:
    """The payer's rate card (None = private pay) in effect on a Texas date."""
    cards = RateCard.objects.filter(payer_id=payer.pk if payer else None, effective_from__lte=day)
    for card in cards.order_by("-effective_from"):
        if card.effective_to is None or card.effective_to >= day:
            return card
    who = payer.name if payer else "private pay"
    raise PricingError(f"No rate card for {who} on {day:%b %d, %Y}.")


def card_for(trip: Trip) -> RateCard:
    return card_on(trip.payer if trip.payer_id else None, local(trip.scheduled_pickup_datetime).date())


def is_after_hours(card: RateCard, when) -> bool:
    when = local(when)
    if card.weekends_after_hours and when.weekday() >= 5:
        return True
    if card.after_hours_start and card.after_hours_end:
        start, end, t = card.after_hours_start, card.after_hours_end, when.time()
        return start <= t < end if start <= end else (t >= start or t < end)
    return False


def _step_time(trip: Trip, step):
    event = trip.events.filter(event_type=step).order_by("occurred_at").first()
    return event.occurred_at if event else None


def wait_minutes(trip: Trip) -> int | None:
    """A wait-and-return ride: from the outbound drop-off to this pickup, by the driver app's clock."""
    if not (trip.driver_waits and trip.outbound_trip_id):
        return None
    dropped = _step_time(trip.outbound_trip, TripEventType.DROPPED_OFF)
    picked = _step_time(trip, TripEventType.PICKED_UP)
    if dropped is None or picked is None:
        return None
    return max(0, math.ceil((picked - dropped).total_seconds() / 60))


def price(trip: Trip) -> tuple[RateCard, list[Line]]:
    """The rate card and charge lines for one completed or no-show trip."""
    if trip.status not in BILLED_STATUSES:
        raise PricingError("Only completed and no-show trips are billed.")
    card = card_for(trip)
    if trip.status == TripStatus.NO_SHOW:
        return card, [Line("No-show fee", Decimal(1), card.no_show_fee)]

    service = trip.service_type
    if card.base_for(service) is None:
        raise PricingError(f"“{card}” has no rate for {trip.get_service_type_display().lower()} trips.")
    lines = []
    # A card can charge the booking and loading fees once for a round trip (ADR 0048):
    # the ride back then pays its miles, after-hours and wait only.
    if card.fees_on_return or trip.leg != TripLeg.RETURN:
        lines += fee_lines(card, service, trip.get_service_type_display())
    per_mile = card.per_mile_for(service)
    if per_mile:
        if trip.billable_miles is None:
            raise PricingError("Add the trip's billable miles (this card charges per mile).")
        extra = max(Decimal(0), trip.billable_miles - card.included_miles)
        if extra:
            lines.append(Line(miles_label(card), extra, per_mile))
    if card.after_hours_fee and is_after_hours(card, trip.scheduled_pickup_datetime):
        lines.append(Line("After-hours", Decimal(1), card.after_hours_fee))
    wait_rate = card.wait_rate_for(service)
    if wait_rate and trip.driver_waits:
        minutes = wait_minutes(trip)
        if minutes is None:
            raise PricingError("The wait can't be measured: the drop-off or pickup step is missing in the driver app.")
        blocks = card.wait_blocks(minutes)
        if blocks:
            lines.append(Line(card.wait_label(minutes), Decimal(blocks), wait_rate))
    return card, lines


def fee_lines(card: RateCard, service: str, service_name: str) -> list[Line]:
    """The booking fee (base rate) and, where the card has one, the loading fee for one ride."""
    lines = [Line(f"{service_name} trip", Decimal(1), card.base_for(service))]
    loading = card.loading_for(service)
    if loading:
        lines.append(Line("Loading", Decimal(1), loading))
    return lines


def miles_label(card: RateCard) -> str:
    return "Miles" if not card.included_miles else f"Miles beyond {card.included_miles} included"


def total(lines: list[Line]) -> Decimal:
    return _money(sum((line.amount for line in lines), Decimal(0)))


# --- Quotes: the price before the ride (ADR 0047) ---------------------------

def quote_payer(trip_request):
    """Who a trip made from this request is billed to: the linked passenger's payer, else private pay (None)."""
    passenger = trip_request.passenger
    return passenger.payer if passenger is not None and passenger.payer_id else None


def quote(trip_request, *, miles=None, wait_minutes=0) -> tuple[RateCard, list[Line], list[str]]:
    """
    The rate card, charge lines and notes for staff for a ride that hasn't
    happened yet. Priced the way its trips will be invoiced: each leg is a
    trip (base + miles + after-hours), and a wait-and-return ride adds the
    wait. `miles` is one way; the ride back is the same route reversed.
    """
    payer = quote_payer(trip_request)
    if payer is not None and payer.payer_type in PROGRAM_PAYER_TYPES:
        # Medicaid and broker rides are paid at the program's rates: the rider owes nothing.
        raise PricingError(f"This rider's trips are paid by {payer.name}: there is no price to email.")
    card = card_on(payer, local(trip_request.requested_datetime).date())
    service, service_name = trip_request.service_type, trip_request.get_service_type_display()
    if card.base_for(service) is None:
        raise PricingError(f"“{card}” has no rate for {service_name.lower()} trips.")
    per_mile = card.per_mile_for(service)
    extra_miles = Decimal(0)
    if per_mile:
        if miles is None:
            raise PricingError("Enter the one-way miles (this card charges per mile).")
        extra_miles = max(Decimal(0), miles - card.included_miles)

    round_trip = trip_request.return_ride != ReturnRide.NONE
    legs = [("Ride there" if round_trip else "", trip_request.requested_datetime)]
    if round_trip:
        timed = trip_request.return_ride == ReturnRide.SCHEDULED and trip_request.return_datetime
        legs.append(("Ride back", trip_request.return_datetime if timed else None))

    def label(leg, text):
        return f"{leg}: {text[0].lower()}{text[1:]}" if leg else text

    lines, notes = [], []
    has_after_hours = bool(card.after_hours_fee) and bool(card.weekends_after_hours or card.after_hours_start)
    for index, (leg, when) in enumerate(legs):
        if card.fees_on_return:
            lines += [Line(label(leg, fee.label), fee.quantity, fee.unit)
                      for fee in fee_lines(card, service, service_name)]
        elif index == 0:
            # Charged once for the whole booking, so not named after either ride.
            lines += fee_lines(card, service, service_name)
        if extra_miles:
            lines.append(Line(label(leg, miles_label(card)), extra_miles, per_mile))
        if not has_after_hours:
            continue
        if when is None:
            notes.append("The ride back has no set time, so no after-hours fee is included for it.")
        elif is_after_hours(card, when):
            lines.append(Line(label(leg, "After-hours"), Decimal(1), card.after_hours_fee))

    wait_rate = card.wait_rate_for(service)
    if trip_request.return_ride == ReturnRide.WAIT and wait_rate:
        blocks = card.wait_blocks(wait_minutes)
        if blocks:
            lines.append(Line(card.wait_label(wait_minutes), Decimal(blocks), wait_rate))
        notes.append("The invoice uses the wait the driver app records, so it can differ from this price.")
    if not total(lines):
        raise PricingError(f"Nothing to charge on “{card}”.")
    if total(lines) > MAX_QUOTE:
        raise PricingError(f"${total(lines):,.2f} is too much to be right: check the miles and the wait.")
    return card, lines, notes


def covered_wait_minutes(trip_request, card: RateCard, wait_minutes: int) -> int | None:
    """Minutes of waiting a quote's price covers, or None when the wait isn't charged."""
    if trip_request.return_ride != ReturnRide.WAIT or not card.wait_rate_for(trip_request.service_type):
        return None
    return card.wait_free_minutes + card.wait_blocks(wait_minutes) * max(1, card.wait_block_minutes)


def trips_to_invoice(payer, start, end):
    """Completed and no-show trips for this payer (None = private pay), Texas dates start..end, not yet invoiced."""
    begin = datetime.combine(start, time.min, tzinfo=_zone())
    finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=_zone())
    invoiced = Invoice.objects.exclude(status=InvoiceStatus.VOID).values("trip_id")
    return (
        Trip.objects.filter(
            payer=payer, status__in=BILLED_STATUSES,
            scheduled_pickup_datetime__gte=begin, scheduled_pickup_datetime__lt=finish,
        )
        .exclude(pk__in=invoiced)
        .select_related("payer", "passenger", "outbound_trip")
        .order_by("scheduled_pickup_datetime")
    )


@transaction.atomic
def create_invoices(payer, start, end, *, user) -> tuple[list[Invoice], list[tuple[Trip, str]]]:
    """Draft invoices for every billable trip in the range. Returns (made, [(trip, why not)])."""
    made, skipped = [], []
    for trip in trips_to_invoice(payer, start, end):
        try:
            card, lines = price(trip)
        except PricingError as exc:
            skipped.append((trip, str(exc)))
            continue
        amount = total(lines)
        if not amount:
            skipped.append((trip, f"Nothing to bill on “{card}”."))
            continue
        invoice = Invoice.objects.create(
            trip=trip, payer=trip.payer, amount=amount, status=InvoiceStatus.DRAFT,
            rate_card=card, lines=[line.as_dict() for line in lines],
        )
        record_change(actor=user, action="created", resource_type="Invoice", resource_id=invoice.pk,
                      after={"trip": str(trip.pk), "amount": str(amount), "rate_card": str(card.pk)}, source="admin")
        made.append(invoice)
    return made, skipped


# --- Driver pay ----------------------------------------------------------

def plan_for(driver, day) -> DriverPayPlan | None:
    plans = DriverPayPlan.objects.filter(driver=driver, effective_from__lte=day).order_by("-effective_from")
    for plan in plans:
        if plan.effective_to is None or plan.effective_to >= day:
            return plan
    return None


def trip_charge(trip: Trip) -> Decimal | None:
    """What the trip is billed: its invoice if there is one, else the rate card's figure."""
    invoice = trip.invoices.exclude(status=InvoiceStatus.VOID).order_by("-created_at").first()
    if invoice:
        return invoice.amount
    try:
        return total(price(trip)[1])
    except PricingError:
        return None


def trip_pay(trip: Trip, plan: DriverPayPlan) -> tuple[list[Line], list[str]]:
    lines, notes = [], []
    flat = plan.per_trip_wheelchair if trip.service_type == "wheelchair" and plan.per_trip_wheelchair is not None \
        else plan.per_trip
    if flat:
        lines.append(Line("Per trip", Decimal(1), flat))
    if plan.per_mile:
        if trip.billable_miles is None:
            notes.append("no billable miles on the trip")
        else:
            lines.append(Line("Per mile", trip.billable_miles, plan.per_mile))
    if plan.percent_of_charge:
        charge = trip_charge(trip)
        if charge is None:
            notes.append("trip charge unknown (no invoice or rate card)")
        else:
            lines.append(Line(f"{plan.percent_of_charge}% of ${charge}", charge, plan.percent_of_charge / 100))
    return lines, notes


def statement(driver, start, end) -> dict:
    """A driver's pay for Texas dates start..end: each trip, each day's hours, and the total."""
    begin = datetime.combine(start, time.min, tzinfo=_zone())
    finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=_zone())
    rows, total_pay, notes = [], Decimal(0), []
    trips = Trip.objects.filter(
        driver=driver, status__in=BILLED_STATUSES,
        scheduled_pickup_datetime__gte=begin, scheduled_pickup_datetime__lt=finish,
    ).select_related("passenger", "payer").order_by("scheduled_pickup_datetime")
    for trip in trips:
        day = local(trip.scheduled_pickup_datetime).date()
        plan = plan_for(driver, day)
        if plan is None:
            rows.append({"trip": trip, "lines": [], "amount": Decimal(0), "notes": ["no pay plan for this date"]})
            continue
        lines, trip_notes = trip_pay(trip, plan)
        amount = total(lines)
        total_pay += amount
        rows.append({"trip": trip, "lines": lines, "amount": amount, "notes": trip_notes})

    hours_rows = []
    checks = VehicleCheck.objects.filter(driver=driver, service_date__gte=start, service_date__lte=end)
    for check in checks.order_by("service_date"):
        plan = plan_for(driver, check.service_date)
        if plan is None or not plan.hourly:
            continue
        if check.ended_at is None:
            notes.append(f"{check.service_date:%b %d}: no end-of-day odometer, so no hours.")
            continue
        hours = Decimal((check.ended_at - check.checked_at).total_seconds() / 3600).quantize(CENT)
        line = Line(f"Hours on duty {check.service_date:%b %d}", hours, plan.hourly)
        total_pay += line.amount
        hours_rows.append({"check": check, "line": line})
    return {"rows": rows, "hours": hours_rows, "total": _money(total_pay), "notes": notes}

"""
Trip profit and 1099 year-end totals (ADR 0041), both worked out from
what's already recorded: invoices or rate cards for the charge, pay plans
for driver pay, the trip's billable miles for mileage cost. Nothing is
stored; a number that can't be worked out is listed, never guessed.
"""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from drivers.models import Driver, EmploymentType
from trips.models import Trip
from vehicles.models import VehicleCheck

from . import pricing
from .pricing import BILLED_STATUSES, _money, _zone

PRIVATE = "Private pay"


def _range(start, end):
    return (datetime.combine(start, time.min, tzinfo=_zone()),
            datetime.combine(end + timedelta(days=1), time.min, tzinfo=_zone()))


def trip_profit(trip: Trip, cost_per_mile: Decimal | None) -> dict:
    """Charge, driver pay, mileage cost and profit for one trip; profit is None when a number is missing."""
    notes = []
    charge = pricing.trip_charge(trip)
    if charge is None:
        notes.append("charge unknown (no invoice, and the rate card can't price it)")

    pay = None
    if not trip.driver_id:
        notes.append("no driver on the trip")
    else:
        plan = pricing.plan_for(trip.driver, pricing.local(trip.scheduled_pickup_datetime).date())
        if plan is None:
            notes.append("driver has no pay plan for this date")
        else:
            lines, pay_notes = pricing.trip_pay(trip, plan)
            if pay_notes:
                notes.extend(f"pay: {note}" for note in pay_notes)
            else:
                pay = pricing.total(lines)

    mileage = Decimal(0)
    if cost_per_mile:
        if trip.billable_miles is None:
            mileage = None
            notes.append("no billable miles, so no mileage cost")
        else:
            mileage = _money(trip.billable_miles * cost_per_mile)

    known = charge is not None and pay is not None and mileage is not None
    return {"trip": trip, "charge": charge, "pay": pay, "mileage": mileage,
            "profit": _money(charge - pay - mileage) if known else None, "notes": notes}


def _hourly_pay(start, end) -> tuple[Decimal, list[str]]:
    """Hourly pay is per day on duty, not per trip, so it comes off the total, not off any one trip."""
    paid, notes = Decimal(0), []
    checks = VehicleCheck.objects.filter(service_date__gte=start, service_date__lte=end).select_related("driver")
    for check in checks.order_by("service_date"):
        plan = pricing.plan_for(check.driver, check.service_date)
        if plan is None or not plan.hourly:
            continue
        if check.ended_at is None:
            notes.append(f"{check.driver.legal_name}, {check.service_date:%b %d}: no end-of-day odometer, "
                         "so no hours.")
            continue
        hours = Decimal((check.ended_at - check.checked_at).total_seconds() / 3600).quantize(pricing.CENT)
        paid += _money(hours * plan.hourly)
    return _money(paid), notes


def profit(start, end, *, cost_per_mile: Decimal | None = None) -> dict:
    """Profit for completed and no-show trips with Texas dates start..end, by payer, then overall."""
    begin, finish = _range(start, end)
    trips = (Trip.objects.filter(status__in=BILLED_STATUSES, scheduled_pickup_datetime__gte=begin,
                                 scheduled_pickup_datetime__lt=finish)
             .select_related("payer", "driver", "passenger", "outbound_trip")
             .order_by("scheduled_pickup_datetime"))
    rows = [trip_profit(trip, cost_per_mile) for trip in trips]

    payers = OrderedDict()
    for row in sorted(rows, key=lambda r: (r["trip"].payer.name if r["trip"].payer_id else PRIVATE)):
        name = row["trip"].payer.name if row["trip"].payer_id else PRIVATE
        group = payers.setdefault(name, {"payer": name, "trips": 0, "counted": 0, "charge": Decimal(0),
                                         "pay": Decimal(0), "mileage": Decimal(0), "profit": Decimal(0)})
        group["trips"] += 1
        if row["profit"] is None:
            continue
        group["counted"] += 1
        for key in ("charge", "pay", "mileage", "profit"):
            group[key] += row[key]
    for group in payers.values():
        group["margin"] = (group["profit"] / group["charge"] * 100).quantize(Decimal("0.1")) \
            if group["charge"] else None

    hourly, hourly_notes = _hourly_pay(start, end)
    counted = sum(g["profit"] for g in payers.values())
    revenue = sum(g["charge"] for g in payers.values())
    return {
        "rows": rows, "payers": list(payers.values()), "hourly": hourly, "notes": hourly_notes,
        "revenue": _money(revenue), "total": _money(counted - hourly),
        "incomplete": [row for row in rows if row["profit"] is None],
        "cost_per_mile": cost_per_mile,
    }


# --- 1099 year-end -------------------------------------------------------

def default_threshold(year: int) -> Decimal:
    """IRS 1099-NEC reporting floor: $600 through 2025; $2,000 for payments from 2026 (inflation-indexed later)."""
    return Decimal("600") if year <= 2025 else Decimal("2000")


def year_end(year: int, threshold: Decimal) -> list[dict]:
    """Each contractor driver's calculated pay for the calendar year, and whether it reaches the threshold."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    result = []
    for driver in Driver.objects.filter(employment_type=EmploymentType.CONTRACTOR).order_by("legal_name"):
        pay = pricing.statement(driver, start, end)
        unpaid = sum(1 for row in pay["rows"] if row["notes"]) + len(pay["notes"])
        if not pay["total"] and not unpaid:
            continue
        result.append({"driver": driver, "total": pay["total"], "trips": len(pay["rows"]),
                       "flagged": unpaid, "reportable": pay["total"] >= threshold})
    return result

"""
The SLA dashboard (ADR 0037): how a week went, per payer, measured the
way brokers grade us. Everything comes from data already collected:
trips and their driver-app timestamps (ADR 0029) and logged turn-downs
(ADR 0028).

- On time: the driver tapped "Arrived at pickup" (or, if that was
  skipped, "Picked up") no later than ON_TIME_GRACE after the scheduled
  pickup. Will-call rides have no fixed time and aren't measured; rides
  with no driver-app arrival can't be measured either, and are counted
  separately so gaps are visible.
- Acceptance: trips we took on that week out of everything offered,
  i.e. accepted / (accepted + turned down). A round trip counts once;
  trips made by standing orders aren't offers.
- Below TARGET_PERCENT shows red.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

from .models import Trip, TripEventType, TripLeg, TripStatus, TurnDown, TurnDownReason

ON_TIME_GRACE = timedelta(minutes=15)
TARGET_PERCENT = 95
TREND_WEEKS = 8
ARRIVAL_STEPS = (TripEventType.ARRIVED_PICKUP, TripEventType.PICKED_UP)


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def week_of(day: date) -> date:
    """The Monday of the week (Texas calendar) containing this day."""
    return day - timedelta(days=day.weekday())


def _bounds(monday: date):
    start = datetime.combine(monday, time.min, tzinfo=_zone())
    return start, start + timedelta(days=7)


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _group(obj) -> str:
    """Per payer; a trip or turn-down without one is grouped by how it reached us."""
    return obj.payer.name if obj.payer_id else obj.get_source_display()


@dataclass
class Row:
    name: str
    trips: int = 0
    completed: int = 0
    no_shows: int = 0
    cancelled: int = 0
    measured: int = 0
    on_time: int = 0
    not_measured: int = 0
    accepted: int = 0
    turned_down: int = 0
    reasons: Counter = field(default_factory=Counter)

    @property
    def late(self) -> int:
        return self.measured - self.on_time

    @property
    def on_time_percent(self):
        return _percent(self.on_time, self.measured)

    @property
    def acceptance_percent(self):
        return _percent(self.accepted, self.accepted + self.turned_down)

    @property
    def no_show_percent(self):
        return _percent(self.no_shows, self.completed + self.no_shows)

    @property
    def on_time_low(self) -> bool:
        return self.on_time_percent is not None and self.on_time_percent < TARGET_PERCENT

    @property
    def acceptance_low(self) -> bool:
        return self.acceptance_percent is not None and self.acceptance_percent < TARGET_PERCENT

    def add(self, other: "Row") -> None:
        for name in ("trips", "completed", "no_shows", "cancelled", "measured", "on_time",
                     "not_measured", "accepted", "turned_down"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.reasons.update(other.reasons)


def arrival_time(trip: Trip):
    for event in trip.events.all():  # ordered by time
        if event.event_type in ARRIVAL_STEPS:
            return event.occurred_at
    return None


def week_rows(monday: date) -> tuple[list[Row], Row]:
    """(one row per payer, sorted by name; the all-payers total) for the week starting Monday."""
    start, end = _bounds(monday)
    rows: dict[str, Row] = {}

    def row(name: str) -> Row:
        return rows.setdefault(name, Row(name))

    trips = (
        Trip.objects.filter(scheduled_pickup_datetime__gte=start, scheduled_pickup_datetime__lt=end)
        .select_related("payer")
        .prefetch_related("events")
    )
    for trip in trips:
        r = row(_group(trip))
        r.trips += 1
        if trip.status == TripStatus.COMPLETED:
            r.completed += 1
        elif trip.status == TripStatus.NO_SHOW:
            r.no_shows += 1
        elif trip.status == TripStatus.CANCELLED:
            r.cancelled += 1
        if trip.status == TripStatus.CANCELLED or trip.will_call:
            continue
        arrived = arrival_time(trip)
        if arrived is None:
            if trip.status in (TripStatus.COMPLETED, TripStatus.NO_SHOW):
                r.not_measured += 1
            continue
        r.measured += 1
        if arrived <= trip.scheduled_pickup_datetime + ON_TIME_GRACE:
            r.on_time += 1

    offers = (
        Trip.objects.filter(created_at__gte=start, created_at__lt=end, standing_order__isnull=True)
        .exclude(leg=TripLeg.RETURN)
        .select_related("payer")
    )
    for trip in offers:
        row(_group(trip)).accepted += 1
    for turn_down in TurnDown.objects.filter(offered_at__gte=start, offered_at__lt=end).select_related("payer"):
        r = row(_group(turn_down))
        r.turned_down += 1
        r.reasons[turn_down.reason] += 1

    total = Row("All payers")
    for r in rows.values():
        total.add(r)
    return sorted(rows.values(), key=lambda r: r.name.lower()), total


def build_sla(monday: date) -> dict:
    rows, total = week_rows(monday)
    labels = dict(TurnDownReason.choices)
    trend = []
    for weeks_back in range(TREND_WEEKS - 1, -1, -1):
        start = monday - timedelta(weeks=weeks_back)
        _, week_total = week_rows(start)
        trend.append({"monday": start, "row": week_total})
    return {
        "monday": monday,
        "sunday": monday + timedelta(days=6),
        "rows": rows,
        "total": total,
        "reasons": [(labels.get(k, k), n) for k, n in total.reasons.most_common()],
        "trend": trend,
        "target": TARGET_PERCENT,
        "grace_minutes": int(ON_TIME_GRACE.total_seconds() // 60),
    }

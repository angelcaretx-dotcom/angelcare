"""
Standing orders (ADR 0035): turning a repeating ride into ordinary
trips, 14 days ahead. Runs when staff save an order and once a day
(the Vercel cron calls run_daily), and is safe to run any number of
times: each leg is made once per date.

Trips dispatch has already changed are never touched by a refresh; only
untouched future ones are re-made when the order changes.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.services import record_change
from notifications.services import NotificationService
from transportation.models import ReturnRide

from .models import StandingOrder, Trip, TripLeg, TripStatus
from .returns import return_leg_for

logger = logging.getLogger(__name__)

HORIZON_DAYS = 14
AUTH_WARNING_DAYS = 14
# A trip saved within this long of being created hasn't been edited since.
UNTOUCHED = timedelta(seconds=2)


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def texas_today():
    return timezone.now().astimezone(_zone()).date()


def _at(day, time_of_day):
    return datetime.combine(day, time_of_day, tzinfo=_zone()) if time_of_day else None


def scheduled_dates(order: StandingOrder, today=None) -> list:
    """The dates in the next 14 days this order should have a ride on."""
    today = today or texas_today()
    last = today + timedelta(days=HORIZON_DAYS - 1)
    if order.end_date:
        last = min(last, order.end_date)
    if order.prior_authorization_ends:
        last = min(last, order.prior_authorization_ends)
    skipped = set(order.skips.values_list("date", flat=True))
    now = timezone.now()
    days = []
    day = max(today, order.start_date)
    while day <= last:
        if day.weekday() in order.weekdays and day not in skipped and _at(day, order.pickup_time) > now:
            days.append(day)
        day += timedelta(days=1)
    return days


def _assign_regulars(trip: Trip, order: StandingOrder) -> None:
    """Give the trip the order's regular driver/vehicle, keeping only those that pass the usual checks."""
    trip.driver_id = order.driver_id
    trip.vehicle_id = order.vehicle_id
    _keep_only_valid_assignment(trip)


def _keep_only_valid_assignment(trip: Trip) -> None:
    """Drop a driver/vehicle that fails the checks (licence, credentials, insurance...): dispatch assigns instead."""
    try:
        trip.full_clean()
    except ValidationError as exc:
        problems = exc.message_dict
        if "driver" in problems:
            trip.driver_id = None
        if "vehicle" in problems:
            trip.vehicle_id = None
        trip.full_clean()  # anything else is a real problem with the order


def _audit(trip: Trip, order: StandingOrder, actor) -> None:
    record_change(
        actor=actor,
        action="created",
        resource_type="Trip",
        resource_id=trip.pk,
        after={"standing_order": str(order.pk), "date": trip.standing_date.isoformat(), "leg": trip.leg},
        source="standing_order",
    )


@transaction.atomic
def generate(order: StandingOrder, *, actor=None, today=None) -> list[Trip]:
    """Create the missing trips for the next 14 days. Returns the trips made."""
    order = StandingOrder.objects.select_for_update().get(pk=order.pk)
    if not order.active:
        return []
    have = set(
        order.trips.exclude(leg=TripLeg.RETURN).values_list("standing_date", flat=True)
    )
    made = []
    for day in scheduled_dates(order, today):
        if day in have:
            continue
        outbound = Trip(
            passenger_id=order.passenger_id,
            source=order.source,
            subcontract_partner=order.subcontract_partner,
            payer_id=order.payer_id,
            prior_authorization_number=order.prior_authorization_number,
            leg=TripLeg.ONE_WAY if order.return_ride == ReturnRide.NONE else TripLeg.OUTBOUND,
            service_type=order.service_type,
            pickup_address=order.pickup_address,
            dropoff_address=order.dropoff_address,
            scheduled_pickup_datetime=_at(day, order.pickup_time),
            appointment_datetime=_at(day, order.appointment_time),
            status=TripStatus.SCHEDULED,
            notes=order.notes,
            standing_order=order,
            standing_date=day,
        )
        _assign_regulars(outbound, order)
        outbound.save()
        _audit(outbound, order, actor)
        made.append(outbound)

        if order.return_ride != ReturnRide.NONE:
            back = return_leg_for(outbound, order.return_ride, _at(day, order.return_time))
            back.standing_order = order
            back.standing_date = day
            back.notes = order.notes
            if not back.driver_waits:
                _assign_regulars(back, order)
            else:
                _keep_only_valid_assignment(back)
            back.save()
            _audit(back, order, actor)
            made.append(back)
    return made


def _untouched_future(order: StandingOrder, from_day):
    """
    Future trips nobody has changed, whole dates only: if dispatch edited
    either leg of a date (or a driver started it), that date is kept.
    """
    by_day = {}
    for trip in order.trips.filter(standing_date__gte=from_day).prefetch_related("events"):
        by_day.setdefault(trip.standing_date, []).append(trip)
    untouched = []
    for trips in by_day.values():
        if all(
            t.status == TripStatus.SCHEDULED and not t.events.all() and t.updated_at - t.created_at < UNTOUCHED
            for t in trips
        ):
            untouched += trips
    return untouched


@transaction.atomic
def refresh(order: StandingOrder, *, actor=None) -> tuple[list[Trip], int]:
    """
    After the order changes: re-make the untouched future trips so they
    match it. Returns (trips made, trips dispatch had changed and were kept).
    """
    today = texas_today()
    untouched = _untouched_future(order, today)
    # Return legs first: they point at their outbound trip.
    for trip in sorted(untouched, key=lambda t: t.leg != TripLeg.RETURN):
        record_change(
            actor=actor,
            action="deleted",
            resource_type="Trip",
            resource_id=trip.pk,
            before={"standing_order": str(order.pk), "date": trip.standing_date.isoformat(), "leg": trip.leg},
            source="standing_order",
        )
        trip.delete()
    kept = (
        order.trips.filter(standing_date__gte=today, status=TripStatus.SCHEDULED)
        .exclude(leg=TripLeg.RETURN)
        .count()
    )
    # Dates that still have a kept, dispatcher-edited trip aren't re-made;
    # a kept trip on a now-skipped date is cancelled.
    skipped = set(order.skips.values_list("date", flat=True))
    for trip in order.trips.filter(standing_date__in=skipped, status=TripStatus.SCHEDULED):
        trip.status = TripStatus.CANCELLED
        trip.save(update_fields=["status", "updated_at"])
        record_change(
            actor=actor,
            action="status_changed",
            resource_type="Trip",
            resource_id=trip.pk,
            before={"status": TripStatus.SCHEDULED},
            after={"status": TripStatus.CANCELLED, "via": "standing_order_skip"},
            source="standing_order",
        )
    return generate(order, actor=actor, today=today), kept


def auth_ending_soon(order: StandingOrder, today=None) -> bool:
    today = today or texas_today()
    ends = order.prior_authorization_ends
    return bool(ends and today <= ends <= today + timedelta(days=AUTH_WARNING_DAYS))


def run_daily() -> dict:
    """The daily job: top up every active order, and warn about ending authorizations."""
    today = texas_today()
    made = failed = warned = 0
    for order in StandingOrder.objects.filter(active=True).select_related("passenger"):
        try:
            made += len(generate(order, today=today))
        except Exception:  # noqa: BLE001 - one bad order mustn't stop the others
            failed += 1
            logger.exception("Standing order %s couldn't create trips.", order.pk)
        if auth_ending_soon(order, today) and order.auth_warning_sent_for != order.prior_authorization_ends:
            NotificationService().notify_prior_auth_ending(order)
            StandingOrder.objects.filter(pk=order.pk).update(auth_warning_sent_for=order.prior_authorization_ends)
            warned += 1
    return {"trips_created": made, "orders_failed": failed, "auth_warnings": warned}

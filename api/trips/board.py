"""
Day board (ADR 0028): one operations day on one page. Unassigned trips
on top with a quick-assign form, then each driver's trips in pickup
order. Days are Texas days (OPERATIONS_TIME_ZONE), not UTC days.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.services import record_change
from drivers.models import Driver, DriverStatus
from vehicles.models import Vehicle, VehicleStatus

from .models import Trip, TripStatus


@dataclass
class DriverColumn:
    driver: Driver
    trips: list = field(default_factory=list)


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def _parse_day(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return timezone.now().astimezone(_zone()).date()


def day_trips(day: date):
    start = datetime.combine(day, time.min, tzinfo=_zone())
    return (
        Trip.objects.filter(
            scheduled_pickup_datetime__gte=start,
            scheduled_pickup_datetime__lt=start + timedelta(days=1),
        )
        .select_related("passenger", "driver", "vehicle", "payer")
        .prefetch_related("events")
        .order_by("scheduled_pickup_datetime")
    )


def build_board(day: date) -> dict:
    unassigned, columns, closed = [], {}, 0
    for trip in day_trips(day):
        events = list(trip.events.all())
        trip.last_event = events[-1] if events else None
        if trip.status in (TripStatus.CANCELLED, TripStatus.NO_SHOW):
            closed += 1
        if trip.is_assigned:
            columns.setdefault(trip.driver_id, DriverColumn(trip.driver)).trips.append(trip)
        elif trip.status == TripStatus.SCHEDULED:
            unassigned.append(trip)
        else:
            # A cancelled/no-show trip that was never assigned: nothing to do.
            continue
    drivers = sorted(columns.values(), key=lambda c: c.driver.legal_name)
    return {
        "unassigned": unassigned,
        "drivers": drivers,
        "trip_count": sum(len(c.trips) for c in drivers) + len(unassigned),
        "closed_count": closed,
    }


def board_urls(model_admin):
    site = model_admin.admin_site

    def board(request):
        if not model_admin.has_view_permission(request):
            raise PermissionDenied
        day = _parse_day(request.GET.get("date"))
        record_change(
            actor=request.user,
            action="viewed",
            resource_type="DayBoard",
            resource_id=day.isoformat(),
            source="admin",
        )
        context = {
            **site.each_context(request),
            "title": f"Day board — {day:%A, %B} {day.day}, {day.year}",
            "day": day,
            "zone": settings.OPERATIONS_TIME_ZONE,
            "previous_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1),
            "today": timezone.now().astimezone(_zone()).date(),
            "can_assign": model_admin.has_change_permission(request),
            "active_drivers": Driver.objects.filter(status=DriverStatus.ACTIVE).order_by("legal_name"),
            "active_vehicles": Vehicle.objects.filter(status=VehicleStatus.ACTIVE).order_by("make", "model"),
            "add_url": reverse("admin:trips_trip_add"),
            **build_board(day),
        }
        return TemplateResponse(request, "admin/trips/board.html", context)

    @require_POST
    def assign(request, trip_id):
        if not model_admin.has_change_permission(request):
            raise PermissionDenied
        trip = get_object_or_404(Trip, pk=trip_id)
        day = timezone.localtime(trip.scheduled_pickup_datetime, _zone()).date()
        back = f"{reverse('admin:trips_trip_board')}?date={day.isoformat()}"

        before = {"driver": _str(trip.driver_id), "vehicle": _str(trip.vehicle_id)}
        trip.driver_id = request.POST.get("driver") or None
        trip.vehicle_id = request.POST.get("vehicle") or None
        if not (trip.driver_id and trip.vehicle_id):
            messages.error(request, "Choose both a driver and a vehicle.")
            return redirect(back)
        try:
            trip.full_clean()
        except ValidationError as exc:
            problems = [m for msgs in exc.message_dict.values() for m in msgs]
            messages.error(request, "Couldn't assign: " + " ".join(problems))
            return redirect(back)
        trip.save()
        record_change(
            actor=request.user,
            action="assigned",
            resource_type="Trip",
            resource_id=trip.id,
            before=before,
            after={"driver": _str(trip.driver_id), "vehicle": _str(trip.vehicle_id)},
            source="admin",
        )
        messages.success(
            request,
            f"Assigned {trip.driver.legal_name} with {trip.vehicle.make} {trip.vehicle.model} "
            f"({trip.vehicle.license_plate}).",
        )
        return redirect(back)

    return [
        path("board/", site.admin_view(board), name="trips_trip_board"),
        path("board/assign/<uuid:trip_id>/", site.admin_view(assign), name="trips_trip_board_assign"),
    ]


def _str(value):
    return str(value) if value else None

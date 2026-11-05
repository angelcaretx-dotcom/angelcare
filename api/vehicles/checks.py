"""
The daily vehicle check (ADR 0034): the one place that records a
vehicle's start-of-day inspection and odometer, its end-of-day
odometer, and an Administrator clearing a failed check. The driver app
and the trip steps (trips.events) call in here.
"""

from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.services import record_change
from notifications.services import NotificationService

from .models import Vehicle, VehicleCheck, VehicleStatus, check_items_for

# More than this in one day is almost certainly a typo.
MAX_MILES_PER_DAY = 1000


class VehicleCheckError(Exception):
    pass


def texas_today() -> date:
    return timezone.now().astimezone(ZoneInfo(settings.OPERATIONS_TIME_ZONE)).date()


def todays_check(vehicle) -> VehicleCheck | None:
    return VehicleCheck.objects.filter(vehicle=vehicle, service_date=texas_today()).first()


def _odometer(value) -> int:
    try:
        reading = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise VehicleCheckError("Type the odometer reading in whole miles, e.g. 48213.") from None
    if not 0 <= reading <= 9_999_999:
        raise VehicleCheckError("That odometer reading doesn't look right.")
    return reading


def _last_reading(vehicle) -> int | None:
    last = VehicleCheck.objects.filter(vehicle=vehicle).order_by("-service_date").first()
    if last is None:
        return None
    return last.end_odometer if last.end_odometer is not None else last.start_odometer


def start_trip_problem(trip) -> str | None:
    """Why the driver can't start this trip yet, or None (checked at the first step)."""
    if not trip.vehicle_id:
        return "No vehicle is assigned to this trip yet. Please call dispatch."
    vehicle = trip.vehicle
    if vehicle.status != VehicleStatus.ACTIVE:
        return f"{vehicle} is {vehicle.get_status_display().lower()}. Please call dispatch."
    if todays_check(vehicle) is None:
        return "Do the daily vehicle check first (on the Today screen)."
    return None


@transaction.atomic
def record_start(vehicle: Vehicle, driver, *, ok_items, odometer, notes: str, user) -> VehicleCheck:
    """
    The pre-trip inspection and start-of-day odometer. Items not ticked
    OK are problems: they need a note, and they take the vehicle out of
    service until an Administrator clears the check.
    """
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    existing = todays_check(vehicle)
    if existing is not None:
        raise VehicleCheckError(
            f"{vehicle} was already checked today by {existing.driver.legal_name}."
        )
    reading = _odometer(odometer)
    last = _last_reading(vehicle)
    if last is not None and reading < last:
        raise VehicleCheckError(
            f"That's lower than the last reading for this vehicle ({last:,}). Check the odometer."
        )
    keys = [key for key, _ in check_items_for(vehicle)]
    failed = [key for key in keys if key not in set(ok_items)]
    notes = notes.strip()
    if failed and not notes:
        raise VehicleCheckError("Describe the problem in the note, so it can be fixed.")

    try:
        with transaction.atomic():
            check = VehicleCheck.objects.create(
                vehicle=vehicle,
                service_date=texas_today(),
                driver=driver,
                start_odometer=reading,
                failed_items=failed,
                notes=notes,
            )
    except IntegrityError:
        raise VehicleCheckError(f"{vehicle} was already checked today.") from None
    record_change(
        actor=user,
        action="vehicle_checked",
        resource_type="VehicleCheck",
        resource_id=check.id,
        after={"vehicle": str(vehicle.pk), "passed": check.passed, "failed_items": failed, "odometer": reading},
        source="driver_app",
    )
    if failed:
        _set_status(vehicle, VehicleStatus.OUT_OF_SERVICE, user=user, via="failed_daily_check", source="driver_app")
        transaction.on_commit(lambda: NotificationService().notify_vehicle_check_failed(check))
    return check


@transaction.atomic
def record_end(check: VehicleCheck, driver, *, odometer, user) -> VehicleCheck:
    check = VehicleCheck.objects.select_for_update().get(pk=check.pk)
    if check.end_odometer is not None:
        raise VehicleCheckError("The end-of-day odometer is already recorded.")
    reading = _odometer(odometer)
    if reading < check.start_odometer:
        raise VehicleCheckError(
            f"That's lower than this morning's reading ({check.start_odometer:,}). Check the odometer."
        )
    if reading - check.start_odometer > MAX_MILES_PER_DAY:
        raise VehicleCheckError(
            f"That's over {MAX_MILES_PER_DAY:,} miles since this morning. Check the odometer."
        )
    check.end_odometer = reading
    check.ended_at = timezone.now()
    check.ended_by = driver
    check.save(update_fields=["end_odometer", "ended_at", "ended_by"])
    record_change(
        actor=user,
        action="vehicle_day_ended",
        resource_type="VehicleCheck",
        resource_id=check.id,
        after={"odometer": reading, "miles": check.miles},
        source="driver_app",
    )
    return check


@transaction.atomic
def clear(check: VehicleCheck, *, note: str, user) -> VehicleCheck:
    """An Administrator confirms the problems are fixed: the vehicle can be used again."""
    check = VehicleCheck.objects.select_for_update().get(pk=check.pk)
    if not check.blocks_vehicle:
        raise VehicleCheckError("This check has nothing to clear.")
    note = note.strip()
    if not note:
        raise VehicleCheckError("Say what was fixed.")
    check.cleared_at = timezone.now()
    check.cleared_by = user
    check.cleared_note = note
    check.save(update_fields=["cleared_at", "cleared_by", "cleared_note"])
    record_change(
        actor=user,
        action="vehicle_check_cleared",
        resource_type="VehicleCheck",
        resource_id=check.id,
        after={"note": note},
        source="admin",
    )
    vehicle = Vehicle.objects.select_for_update().get(pk=check.vehicle_id)
    # Only undo what the failed check did; a vehicle staff put in
    # maintenance for another reason stays there.
    still_blocked = any(c.blocks_vehicle for c in vehicle.checks.exclude(pk=check.pk))
    if vehicle.status == VehicleStatus.OUT_OF_SERVICE and not still_blocked:
        _set_status(vehicle, VehicleStatus.ACTIVE, user=user, via="daily_check_cleared", source="admin")
    return check


def _set_status(vehicle: Vehicle, status: str, *, user, via: str, source: str) -> None:
    if vehicle.status == status:
        return
    previous = vehicle.status
    vehicle.status = status
    vehicle.save(update_fields=["status", "updated_at"])
    record_change(
        actor=user,
        action="status_changed",
        resource_type="Vehicle",
        resource_id=vehicle.id,
        before={"status": previous},
        after={"status": status, "via": via},
        source=source,
    )

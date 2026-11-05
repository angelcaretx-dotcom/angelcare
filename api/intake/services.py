"""
The import steps (ADR 0038): upload -> map -> assess every row ->
create trips from the approved ones. The admin screens call these.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from audit.services import record_change
from passengers.models import Passenger
from transportation.models import ReturnRide
from trips.models import StandingOrder, Trip, TripLeg, TripStatus
from trips.returns import return_leg_for
from trips.standing import keep_only_valid_assignment

from . import extract, mapping
from .models import ImportBatch, ImportRow, RowStatus
from .normalize import assess


class UploadError(Exception):
    pass


@transaction.atomic
def upload(file, *, source, payer, user) -> ImportBatch:
    """Store the original, read its rows, and map them if this sender's layout is known."""
    content = file.read()
    try:
        headers, rows = extract.read_file(content, file.name)
    except extract.ExtractError as exc:
        raise UploadError(str(exc)) from None
    batch = ImportBatch(source=source, payer=payer, original_name=file.name[:255], headers=headers, uploaded_by=user)
    batch.file.save(file.name, ContentFile(content), save=False)
    template = mapping.find_template(source, headers)
    batch.template = template
    batch.field_map = template.field_map if template else mapping.guess(headers)
    batch.save()
    ImportRow.objects.bulk_create(
        ImportRow(batch=batch, number=n, raw=raw) for n, raw in enumerate(rows, start=2)
    )
    record_change(
        actor=user, action="imported", resource_type="ImportBatch", resource_id=batch.pk,
        after={"file": batch.original_name, "rows": len(rows), "source": source,
               "template": template.name if template else None},
        source="admin",
    )
    if template:
        apply_mapping(batch)
    return batch


@transaction.atomic
def apply_mapping(batch: ImportBatch) -> None:
    """(Re-)map every row not yet decided, and check each one."""
    seen = set(
        batch.rows.filter(status=RowStatus.CREATED).exclude(data__trip_id="").values_list("data__trip_id", flat=True)
    )
    for row in batch.rows.exclude(status__in=[RowStatus.CREATED, RowStatus.REJECTED]).order_by("number"):
        row.values = mapping.apply(batch.field_map, row.raw)
        assess(row, seen)
        row.save()


def recheck(row: ImportRow) -> ImportRow:
    """After an edit in review."""
    seen = set(
        row.batch.rows.exclude(pk=row.pk).filter(number__lt=row.number)
        .exclude(status=RowStatus.REJECTED).exclude(data__trip_id="").values_list("data__trip_id", flat=True)
    )
    assess(row, seen)
    row.save()
    return row


def _standing_match(passenger, pickup_at) -> StandingOrder | None:
    """A standing order for this rider on this weekday at this time: its regular driver/vehicle may drive."""
    if passenger is None:
        return None
    local = timezone.localtime(pickup_at, ZoneInfo(settings.OPERATIONS_TIME_ZONE))  # Texas weekday and time
    for order in StandingOrder.objects.filter(passenger=passenger, active=True, pickup_time=local.time()):
        if local.weekday() in order.weekdays:
            return order
    return None


@transaction.atomic
def create_trips(row: ImportRow, *, user) -> ImportRow:
    """
    Turn one approved row into its trip (and ride back). Only rows with
    no hard problems; soft ones (a new rider) are accepted by approving.
    """
    row = ImportRow.objects.select_for_update(of=("self",)).select_related("batch", "batch__payer").get(pk=row.pk)
    if row.status not in (RowStatus.READY, RowStatus.REVIEW):
        return row
    if row.hard_problems:
        raise ValidationError(row.hard_problems)
    batch, data = row.batch, row.data

    passenger = row.passenger
    if passenger is None:
        passenger = Passenger(legal_name=data["rider_name"], phone=data["rider_phone"], payer=batch.payer)
        passenger.full_clean()
        passenger.save()
        record_change(actor=user, action="created", resource_type="Passenger", resource_id=passenger.pk,
                      after={"via": "trip_import", "batch": str(batch.pk)}, source="admin")

    pickup_at = datetime.fromisoformat(data["pickup_at"])
    outbound = Trip(
        passenger=passenger,
        source=batch.source,
        payer=batch.payer,
        broker_trip_number=data["trip_id"][:50],
        prior_authorization_number=data["prior_auth"][:50],
        leg=TripLeg.ONE_WAY if data["return_ride"] == ReturnRide.NONE else TripLeg.OUTBOUND,
        service_type=data["service_type"],
        pickup_address=data["pickup_address"],
        dropoff_address=data["dropoff_address"],
        scheduled_pickup_datetime=pickup_at,
        appointment_datetime=datetime.fromisoformat(data["appointment_at"]) if data["appointment_at"] else None,
        status=TripStatus.SCHEDULED,
        notes=data["notes"],
    )
    order = _standing_match(passenger, pickup_at)
    if order:
        outbound.driver_id, outbound.vehicle_id = order.driver_id, order.vehicle_id
    keep_only_valid_assignment(outbound)  # full_clean; drops a driver/vehicle failing the checks
    outbound.save()
    back = None
    if data["return_ride"] != ReturnRide.NONE:
        return_at = datetime.fromisoformat(data["return_at"]) if data["return_at"] else None
        back = return_leg_for(outbound, data["return_ride"], return_at)
        back.notes = data["notes"]
        keep_only_valid_assignment(back)
        back.save()

    row.passenger, row.trip, row.return_trip = passenger, outbound, back
    row.status, row.decided_by, row.decided_at = RowStatus.CREATED, user, timezone.now()
    row.save()
    for trip in filter(None, (outbound, back)):
        record_change(actor=user, action="created", resource_type="Trip", resource_id=trip.pk,
                      after={"via": "trip_import", "batch": str(batch.pk), "row": row.number, "leg": trip.leg},
                      source="admin")
    return row


def create_ready(batch: ImportBatch, *, user) -> tuple[int, list[str]]:
    """Bulk approve: every ready row. Returns (rows made, failures)."""
    made, failures = 0, []
    for row in batch.rows.filter(status=RowStatus.READY).order_by("number"):
        try:
            create_trips(row, user=user)
            made += 1
        except ValidationError as exc:
            row.status = RowStatus.FAILED
            row.problems = [{"message": m, "hard": True} for m in exc.messages]
            row.save(update_fields=["status", "problems"])
            failures.append(f"Row {row.number}: " + "; ".join(exc.messages))
    return made, failures


def reject(row: ImportRow, *, user) -> None:
    row.status, row.decided_by, row.decided_at = RowStatus.REJECTED, user, timezone.now()
    row.save(update_fields=["status", "decided_by", "decided_at"])
    record_change(actor=user, action="rejected", resource_type="ImportRow", resource_id=row.pk,
                  after={"batch": str(row.batch_id), "row": row.number}, source="admin")

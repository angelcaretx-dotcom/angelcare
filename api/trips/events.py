"""
Recording a step in a trip's timeline (ADR 0029). The one place that
decides which step may follow which and what each does to the trip's
status, so the driver app and any future surface behave the same.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from audit.services import record_change
from vehicles.checks import start_trip_problem

from .models import SignatureMissingReason, Trip, TripEvent, TripEventType, TripStatus
from .signatures import SignatureError, clean_signature

E = TripEventType

# What the driver may tap next, given the last step recorded.
NEXT_STEPS = {
    None: [E.EN_ROUTE, E.ARRIVED_PICKUP],
    E.EN_ROUTE: [E.ARRIVED_PICKUP],
    E.ARRIVED_PICKUP: [E.PICKED_UP, E.NO_SHOW, E.CANCELLED_AT_DOOR],
    E.PICKED_UP: [E.DROPPED_OFF],
    E.DROPPED_OFF: [],
    E.NO_SHOW: [],
    E.CANCELLED_AT_DOOR: [],
}

STATUS_AFTER = {
    E.EN_ROUTE: TripStatus.IN_PROGRESS,
    E.ARRIVED_PICKUP: TripStatus.IN_PROGRESS,
    E.PICKED_UP: TripStatus.IN_PROGRESS,
    E.DROPPED_OFF: TripStatus.COMPLETED,
    E.NO_SHOW: TripStatus.NO_SHOW,
    E.CANCELLED_AT_DOOR: TripStatus.CANCELLED,
}

NEEDS_REASON = {E.NO_SHOW, E.CANCELLED_AT_DOOR}


class TripEventError(Exception):
    pass


def last_step(trip: Trip):
    last = trip.events.order_by("-occurred_at").first()
    return E(last.event_type) if last else None


def next_steps(trip: Trip) -> list:
    if trip.status in (TripStatus.CANCELLED, TripStatus.COMPLETED, TripStatus.NO_SHOW):
        return []
    return NEXT_STEPS[last_step(trip)]


def _coordinate(value, limit):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value)).quantize(Decimal("0.000001"))
    except InvalidOperation:
        return None
    return number if -limit <= number <= limit else None


def _accuracy(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _pickup_signature(signature: str, missing_reason: str, note: str):
    """At pickup the rider signs, or the driver says why not (ADR 0034)."""
    if missing_reason:
        if missing_reason not in SignatureMissingReason.values:
            raise TripEventError("Choose why the rider can't sign.")
        if missing_reason == SignatureMissingReason.OTHER and not note.strip():
            raise TripEventError("Add a note saying why the rider didn't sign.")
        return None, missing_reason
    if not signature:
        raise TripEventError("Ask the rider to sign, or choose why they can't.")
    try:
        return clean_signature(signature), ""
    except SignatureError as exc:
        raise TripEventError(str(exc)) from None


@transaction.atomic
def record_trip_event(
    trip: Trip,
    event_type: str,
    *,
    user,
    latitude=None,
    longitude=None,
    accuracy=None,
    reason: str = "",
    note: str = "",
    signature: str = "",
    signed_by_name: str = "",
    signature_missing_reason: str = "",
    source: str = "driver_app",
) -> TripEvent:
    trip = Trip.objects.select_for_update(of=("self",)).select_related("vehicle").get(pk=trip.pk)
    step = E(event_type)
    if step not in next_steps(trip):
        raise TripEventError(
            f"“{step.label}” can't be recorded now — this trip is "
            f"{trip.get_status_display().lower()}."
        )
    if step in NEEDS_REASON and not reason:
        raise TripEventError("Choose a reason.")
    # The first step starts the trip: the vehicle must have passed
    # today's check (ADR 0034). Ending at the door needs no vehicle.
    if last_step(trip) is None and step not in NEEDS_REASON:
        problem = start_trip_problem(trip)
        if problem:
            raise TripEventError(problem)

    signature_png = None
    if step == E.PICKED_UP:
        signature_png, signature_missing_reason = _pickup_signature(
            signature, signature_missing_reason, note
        )
    else:
        signed_by_name = signature_missing_reason = ""

    lat, lng = _coordinate(latitude, 90), _coordinate(longitude, 180)
    if lat is None or lng is None:
        lat = lng = None
    event = TripEvent.objects.create(
        trip=trip,
        event_type=step,
        latitude=lat,
        longitude=lng,
        accuracy_m=_accuracy(accuracy) if lat is not None else None,
        reason=reason if step in NEEDS_REASON else "",
        note=note.strip(),
        signature=signature_png,
        signed_by_name=signed_by_name.strip()[:200] if signature_png else "",
        signature_missing_reason=signature_missing_reason,
        recorded_by=user,
        source=source,
    )

    new_status = STATUS_AFTER[step]
    if trip.status != new_status:
        previous = trip.status
        trip.status = new_status
        trip.save(update_fields=["status", "updated_at"])
        record_change(
            actor=user,
            action="status_changed",
            resource_type="Trip",
            resource_id=trip.id,
            before={"status": previous},
            after={"status": new_status, "via": step.value},
            source=source,
        )
    return event

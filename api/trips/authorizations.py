"""
Prior authorizations: trips used vs. remaining, and the block when one
is used up or out of dates (ADR 0043).

A trip counts against the authorization whose number it carries (same
rider, same payer) unless it's cancelled. On a round-trip authorization
the ride back doesn't count separately. Dates are Texas dates.
"""

from datetime import timedelta

from django.utils import timezone

from .models import AuthorizationUnit, PriorAuthorization, Trip, TripLeg, TripStatus

COUNTED = (TripStatus.SCHEDULED, TripStatus.IN_PROGRESS, TripStatus.COMPLETED, TripStatus.NO_SHOW)
LOW_TRIPS = 3
LOW_DAYS = 14


def _texas_date(dt):
    from .standing import _zone

    return timezone.localtime(dt, _zone()).date()


def counted_trips(auth: PriorAuthorization):
    trips = Trip.objects.filter(
        prior_authorization_number__iexact=auth.number, passenger_id=auth.passenger_id, status__in=COUNTED
    )
    if auth.counts == AuthorizationUnit.ROUND_TRIP:
        trips = trips.exclude(leg=TripLeg.RETURN)
    return trips


def used(auth: PriorAuthorization) -> int:
    return counted_trips(auth).count()


def remaining(auth: PriorAuthorization) -> int:
    return max(0, auth.trips_authorized - used(auth))


def find(trip: Trip) -> PriorAuthorization | None:
    """The tracked authorization for this trip's number, if one is entered."""
    number = (trip.prior_authorization_number or "").strip()
    if not number:
        return None
    matches = PriorAuthorization.objects.filter(number__iexact=number).select_related("payer", "passenger")
    if trip.payer_id:
        same_payer = [a for a in matches if a.payer_id == trip.payer_id]
        if same_payer:
            return same_payer[0]
    return matches.first()


def _uses(trip: Trip, auth: PriorAuthorization) -> bool:
    return not (auth.counts == AuthorizationUnit.ROUND_TRIP and trip.leg == TripLeg.RETURN)


def problem_for(trip: Trip, *, return_too: bool = False) -> str | None:
    """
    Why this trip can't be scheduled under its authorization, or None.
    `return_too`: a ride back is about to be booked with it (a standing
    order's day), so it needs room for both.
    """
    if trip.status not in COUNTED:
        return None
    trip.fill_payer_from_passenger()  # a blank payer means the passenger's
    auth = find(trip)
    if auth is None:
        return None
    if auth.payer_id != trip.payer_id:
        return f"Authorization {auth.number} is for {auth.payer}, not this trip's payer."
    if auth.passenger_id != trip.passenger_id:
        return f"Authorization {auth.number} belongs to another rider."
    if trip.scheduled_pickup_datetime:
        day = _texas_date(trip.scheduled_pickup_datetime)
        if not auth.valid_from <= day <= auth.valid_to:
            return (f"Authorization {auth.number} covers {auth.valid_from:%b %d, %Y} to {auth.valid_to:%b %d, %Y}; "
                    f"this pickup is {day:%b %d, %Y}. Get a new authorization from {auth.payer}.")
    need = int(_uses(trip, auth)) + int(return_too and auth.counts == AuthorizationUnit.LEG)
    if not need:
        return None
    others = counted_trips(auth).exclude(pk=trip.pk).count() if trip.pk else used(auth)
    if others + need > auth.trips_authorized:
        return (f"All {auth.trips_authorized} trips on authorization {auth.number} are used. "
                f"Get a new authorization from {auth.payer} first.")
    return None


def needs_check(trip: Trip) -> bool:
    """Only when the number, the date or the status changes: trips already booked under it keep working."""
    if trip._state.adding:
        return True
    before = Trip.objects.filter(pk=trip.pk).values(
        "prior_authorization_number", "scheduled_pickup_datetime", "status", "passenger_id", "payer_id"
    ).first()
    if before is None:
        return True
    return (
        before["prior_authorization_number"] != trip.prior_authorization_number
        or before["scheduled_pickup_datetime"] != trip.scheduled_pickup_datetime
        or before["passenger_id"] != trip.passenger_id
        or before["payer_id"] != trip.payer_id
        or (before["status"] not in COUNTED and trip.status in COUNTED)
    )


def running_low(today):
    """Current authorizations with LOW_TRIPS or fewer trips left, or ending within LOW_DAYS: (auth, left) pairs."""
    result = []
    current = PriorAuthorization.objects.filter(valid_to__gte=today - timedelta(days=1)).select_related(
        "payer", "passenger"
    )
    for auth in current.order_by("valid_to"):
        left = remaining(auth)
        if left <= LOW_TRIPS or auth.valid_to <= today + timedelta(days=LOW_DAYS):
            result.append((auth, left))
    return result


def stage(auth: PriorAuthorization, left: int, today) -> int:
    """1 = running low, 2 = used up or ended."""
    return 2 if left == 0 or auth.valid_to < today else 1


def send_alerts(today) -> int:
    """One morning email naming authorizations that newly ran low or ran out. Returns how many."""
    from django.db import transaction

    from notifications.services import NotificationService

    rows = []
    candidates = list(running_low(today))
    ended = PriorAuthorization.objects.filter(valid_to__lt=today, alert_stage__lt=2).select_related("payer")
    candidates += [(auth, remaining(auth)) for auth in ended if all(auth.pk != a.pk for a, _ in candidates)]
    for auth, left in candidates:
        now = stage(auth, left, today)
        if now <= auth.alert_stage:
            continue
        PriorAuthorization.objects.filter(pk=auth.pk).update(alert_stage=now)
        if now == 2:
            status = "used up" if left == 0 else f"ended {auth.valid_to:%b %d, %Y}"
        else:
            status = f"{left} of {auth.trips_authorized} trips left, ends {auth.valid_to:%b %d, %Y}"
        rows.append((f"{auth.reference} ({auth.payer.name})", status))
    if rows:
        transaction.on_commit(lambda: NotificationService().notify_authorizations_low(rows))
    return len(rows)

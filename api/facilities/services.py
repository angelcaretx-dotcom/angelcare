"""
Facility portal accounts and what they can see (ADR 0042).

Accounts are made only by the office, which emails a set-up link (the
same one-time, 3-day links as rider accounts, ADR 0031). Signing in is
two steps: the password, then a 6-digit code emailed to the account
(the website's email codes, ADR 0033). A portal user sees every ride
linked to their facility; bookings arrive as ride requests the office
confirms.
"""

import secrets
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from audit.services import record_change
from notifications.services import NotificationService
from transportation.models import BookedFor, TripRequest
from trips.models import Trip, TripStatus
from verification import services as codes
from verification.models import Purpose

from .models import Facility, FacilityUser, FollowUpDigest, Stage

SOURCE = "facility_portal"
# The signed-in user's id waits here between the password and the code.
PENDING_KEY = "facility_pending_sign_in"
PENDING_TTL = timedelta(minutes=10)


class PortalError(Exception):
    """Shown to the person as is."""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# --- Accounts ------------------------------------------------------------

def _link(path: str, user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return f"{settings.WEBSITE_URL}{path}?uid={uid}&token={default_token_generator.make_token(user)}"


def send_invite(portal_user: FacilityUser) -> None:
    NotificationService().send_facility_invite(portal_user, _link("/facility/set-password", portal_user.user))
    portal_user.invited_at = timezone.now()
    portal_user.save(update_fields=["invited_at", "updated_at"])


@transaction.atomic
def create_portal_user(portal_user: FacilityUser, *, actor) -> FacilityUser:
    """Save a new portal user (facility, email, name filled in) with a login that has no password yet, and invite."""
    portal_user.email = normalize_email(portal_user.email)
    if FacilityUser.objects.filter(email=portal_user.email).exists():
        raise PortalError("That email already has a facility portal account.")
    user = get_user_model()(username=f"facility-{secrets.token_hex(8)}", email=portal_user.email)
    user.set_unusable_password()
    user.save()
    portal_user.user = user
    portal_user.save()
    record_change(actor=actor, action="created", resource_type="FacilityUser", resource_id=portal_user.pk,
                  after={"facility": str(portal_user.facility_id)}, source="admin")
    transaction.on_commit(lambda: send_invite(portal_user))
    return portal_user


def request_password_reset(email: str) -> None:
    """Silent about whether the email has an account."""
    portal_user = FacilityUser.objects.filter(email=normalize_email(email)).select_related("user").first()
    if portal_user is None or not portal_user.user.is_active:
        return
    if portal_user.verified_at is None:
        send_invite(portal_user)
    else:
        NotificationService().send_facility_password_reset(
            portal_user, _link("/facility/reset-password", portal_user.user)
        )


def _user_from_link(uid: str, token: str):
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        user = get_user_model().objects.get(pk=pk, facility_user__isnull=False)
    except (ValueError, TypeError, OverflowError, get_user_model().DoesNotExist):
        return None
    return user if default_token_generator.check_token(user, token) else None


@transaction.atomic
def set_password_from_link(uid: str, token: str, password: str):
    user = _user_from_link(uid, token)
    if user is None or not user.is_active:
        raise PortalError("This link has expired or was already used. Ask for a new one.")
    validate_password(password, user)  # raises django ValidationError
    user.set_password(password)
    user.save(update_fields=["password"])
    portal_user = user.facility_user
    first_time = portal_user.verified_at is None
    if first_time:
        portal_user.verified_at = timezone.now()
        portal_user.save(update_fields=["verified_at", "updated_at"])
    record_change(actor=user, action="facility_account_verified" if first_time else "facility_password_reset",
                  resource_type="FacilityUser", resource_id=portal_user.pk, source=SOURCE)
    return user


# --- Two-step sign-in ----------------------------------------------------

def start_sign_in(session, user) -> str:
    """The password was right: email a code and remember who is signing in. Returns the code's id."""
    verification = codes.start(user.facility_user.email, Purpose.FACILITY_SIGN_IN)
    session[PENDING_KEY] = {
        "user": user.pk, "verification": str(verification.id),
        "until": (timezone.now() + PENDING_TTL).isoformat(),
    }
    return str(verification.id)


def finish_sign_in(session, code: str):
    """The user whose password and code are both right; PortalError otherwise."""
    pending = session.get(PENDING_KEY)
    if not pending or datetime.fromisoformat(pending["until"]) <= timezone.now():
        session.pop(PENDING_KEY, None)
        raise PortalError("Please sign in again.")
    try:
        _, verification = codes.check(pending["verification"], code)
    except codes.VerificationError as exc:
        raise PortalError(str(exc)) from exc
    session.pop(PENDING_KEY, None)
    portal_user = FacilityUser.objects.select_related("user").filter(user_id=pending["user"]).first()
    if (portal_user is None or verification.purpose != Purpose.FACILITY_SIGN_IN
            or verification.email != portal_user.email or not portal_user.user.is_active):
        raise PortalError("Please sign in again.")
    return portal_user.user


# --- What a facility sees ------------------------------------------------

def requests_for(facility: Facility):
    return (
        TripRequest.objects.filter(facility=facility)
        .select_related("trip", "trip__driver", "trip__vehicle", "trip__passenger", "facility_user")
        .order_by("-requested_datetime")
    )


def trips_for(facility: Facility):
    """Rides linked to the facility by the office that didn't come from a portal request (or its ride back)."""
    from_requests = TripRequest.objects.filter(facility=facility)
    return (
        Trip.objects.filter(facility=facility)
        .exclude(trip_request__in=from_requests)
        .exclude(outbound_trip__trip_request__in=from_requests)
        .select_related("passenger", "driver", "vehicle")
        .order_by("-scheduled_pickup_datetime")
    )


TRIP_STATUS_WORDS = {
    TripStatus.SCHEDULED: ("confirmed", "Confirmed"),
    TripStatus.IN_PROGRESS: ("in_progress", "On the way"),
    TripStatus.COMPLETED: ("completed", "Completed"),
    TripStatus.NO_SHOW: ("missed", "Missed"),
    TripStatus.CANCELLED: ("cancelled", "Cancelled"),
}


@transaction.atomic
def book(portal_user: FacilityUser, data: dict) -> TripRequest:
    """A ride request from the portal: the office confirms it, like any website booking."""
    trip_request = TripRequest.objects.create(
        **data, email=portal_user.email, booked_for=BookedFor.FACILITY, source=SOURCE,
        facility=portal_user.facility, facility_user=portal_user,
    )
    record_change(actor=portal_user.user, action="created", resource_type="TripRequest",
                  resource_id=trip_request.pk, after={"facility": str(portal_user.facility_id)}, source=SOURCE)
    transaction.on_commit(lambda: NotificationService().notify_new_trip_request(trip_request))
    return trip_request


def cancel(portal_user: FacilityUser, trip_request: TripRequest) -> None:
    from riders import services as rider_services

    try:
        rider_services.cancel_request(portal_user, trip_request, by="facility", source=SOURCE)
    except rider_services.RiderAccountError as exc:
        raise PortalError(str(exc)) from exc


# --- Follow-ups ----------------------------------------------------------

def due_follow_ups(today: date):
    return (
        Facility.objects.filter(next_follow_up__lte=today)
        .exclude(stage=Stage.LOST)
        .order_by("next_follow_up", "name")
    )


def send_follow_up_digest(today: date) -> int:
    """One morning email listing follow-ups due today or overdue; at most one a day. Returns how many."""
    if FollowUpDigest.objects.filter(day=today).exists():
        return 0
    due = list(due_follow_ups(today))
    if not due:
        return 0
    FollowUpDigest.objects.create(day=today, count=len(due))
    rows = []
    for facility in due:
        late = (today - facility.next_follow_up).days
        when = "today" if late == 0 else f"{facility.next_follow_up:%b %d} ({late} day{'s' if late != 1 else ''} overdue)"
        rows.append((facility.name, f"{facility.follow_up_note or 'Follow up'} — {when}"))
    transaction.on_commit(lambda: NotificationService().notify_facility_follow_ups(rows))
    return len(due)

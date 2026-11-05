"""
Rider accounts (ADR 0031): offering one after a booking, setting and
resetting passwords by emailed link, what a rider can see, cancelling.
"""

import logging
import secrets
from datetime import timedelta

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
from transportation.models import BookedFor, TripRequest, TripRequestStatus
from trips.models import TripStatus

from .models import RiderAccount

logger = logging.getLogger(__name__)

# An unused invite is re-sent on a later booking, but not more than daily.
INVITE_RESEND_AFTER = timedelta(days=1)


class RiderAccountError(Exception):
    pass


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _link(path: str, user) -> str:
    # Django's password-reset tokens: valid PASSWORD_RESET_TIMEOUT (3 days),
    # and void as soon as the password changes -- so each link works once.
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.WEBSITE_URL}{path}?uid={uid}&token={token}"


def send_invite(account: RiderAccount) -> None:
    NotificationService().send_rider_invite(account, _link("/account/set-password", account.user))
    account.invited_at = timezone.now()
    account.save(update_fields=["invited_at", "updated_at"])


def offer_account_for(trip_request: TripRequest) -> None:
    """
    After a website booking: the first booking from a new email gets an
    account and a set-up link; an unused invite is re-sent at most daily.
    Facility bookings never get one. Never raises -- the booking is
    already saved, and an account is optional.
    """
    try:
        if trip_request.booked_for == BookedFor.FACILITY:
            return
        email = normalize_email(trip_request.email)
        account = RiderAccount.objects.filter(email=email).first()
        if account is not None:
            stale = account.invited_at is None or account.invited_at < timezone.now() - INVITE_RESEND_AFTER
            if account.verified_at is None and stale:
                send_invite(account)
            return
        with transaction.atomic():
            user = get_user_model()(username=f"rider-{secrets.token_hex(8)}", email=email)
            user.set_unusable_password()
            user.save()
            account = RiderAccount.objects.create(
                user=user,
                email=email,
                full_name=trip_request.full_name,
                phone=trip_request.phone,
                mobility_notes=trip_request.mobility_notes,
            )
        send_invite(account)
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("Couldn't offer a rider account for trip request %s", trip_request.pk)


def request_password_reset(email: str) -> None:
    """Always silent about whether the email has an account."""
    account = RiderAccount.objects.filter(email=normalize_email(email)).select_related("user").first()
    if account is None:
        return
    if account.verified_at is None:
        send_invite(account)
    else:
        NotificationService().send_rider_password_reset(account, _link("/account/reset-password", account.user))


def user_from_link(uid: str, token: str):
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        user = get_user_model().objects.get(pk=pk, rider_account__isnull=False)
    except (ValueError, TypeError, OverflowError, get_user_model().DoesNotExist):
        return None
    return user if default_token_generator.check_token(user, token) else None


@transaction.atomic
def set_password_from_link(uid: str, token: str, password: str):
    user = user_from_link(uid, token)
    if user is None:
        raise RiderAccountError("This link has expired or was already used. Ask for a new one.")
    validate_password(password, user)  # raises django ValidationError
    user.set_password(password)
    user.save(update_fields=["password"])
    account = user.rider_account
    first_time = account.verified_at is None
    if first_time:
        account.verified_at = timezone.now()
        account.save(update_fields=["verified_at", "updated_at"])
    record_change(
        actor=user,
        action="rider_account_verified" if first_time else "rider_password_reset",
        resource_type="RiderAccount",
        resource_id=account.pk,
        source="rider_portal",
    )
    return user


def requests_for(account: RiderAccount):
    """
    The requests this account may see: those made with its email, except
    facility bookings (other patients, booked by staff who share the inbox).
    """
    return (
        TripRequest.objects.filter(email__iexact=account.email)
        .exclude(booked_for=BookedFor.FACILITY)
        .select_related("trip", "trip__driver", "trip__vehicle")
        .order_by("-requested_datetime")
    )


def trip_of(trip_request):
    try:
        return trip_request.trip
    except TripRequest.trip.RelatedObjectDoesNotExist:
        return None


OPEN_REQUEST_STATUSES = {
    TripRequestStatus.NEW,
    TripRequestStatus.REVIEWED,
    TripRequestStatus.CONTACTED,
    TripRequestStatus.SCHEDULED,
}


def can_cancel(trip_request, now=None) -> bool:
    now = now or timezone.now()
    if trip_request.status not in OPEN_REQUEST_STATUSES:
        return False
    trip = trip_of(trip_request)
    if trip is not None:
        return trip.status == TripStatus.SCHEDULED and trip.scheduled_pickup_datetime > now
    return trip_request.requested_datetime > now


def rider_status(trip_request) -> tuple[str, str]:
    """(key, wording) as the rider should see it."""
    trip = trip_of(trip_request)
    if trip is not None and trip_request.status == TripRequestStatus.SCHEDULED:
        return {
            TripStatus.SCHEDULED: ("confirmed", "Confirmed"),
            TripStatus.IN_PROGRESS: ("in_progress", "On the way"),
            TripStatus.COMPLETED: ("completed", "Completed"),
            TripStatus.NO_SHOW: ("missed", "Missed"),
            TripStatus.CANCELLED: ("cancelled", "Cancelled"),
        }[trip.status]
    return {
        TripRequestStatus.NEW: ("received", "Received — we'll call you to confirm"),
        TripRequestStatus.REVIEWED: ("received", "Received — we'll call you to confirm"),
        TripRequestStatus.CONTACTED: ("contacted", "We've been in touch — awaiting confirmation"),
        TripRequestStatus.SCHEDULED: ("confirmed", "Confirmed"),
        TripRequestStatus.DECLINED: ("declined", "We couldn't take this ride"),
        TripRequestStatus.CANCELLED: ("cancelled", "Cancelled"),
    }[trip_request.status]


@transaction.atomic
def cancel_request(account: RiderAccount, trip_request: TripRequest) -> None:
    trip_request = TripRequest.objects.select_for_update().get(pk=trip_request.pk)
    if not can_cancel(trip_request):
        raise RiderAccountError("This ride can't be cancelled online any more. Please call us.")
    user = account.user
    previous = trip_request.status
    trip_request.status = TripRequestStatus.CANCELLED
    trip_request.save(update_fields=["status", "updated_at"])
    record_change(
        actor=user,
        action="status_changed",
        resource_type="TripRequest",
        resource_id=trip_request.pk,
        before={"status": previous},
        after={"status": TripRequestStatus.CANCELLED, "by": "rider"},
        source="rider_portal",
    )
    trip = trip_of(trip_request)
    if trip is not None:
        trip.status = TripStatus.CANCELLED
        trip.save(update_fields=["status", "updated_at"])
        record_change(
            actor=user,
            action="status_changed",
            resource_type="Trip",
            resource_id=trip.pk,
            before={"status": TripStatus.SCHEDULED},
            after={"status": TripStatus.CANCELLED, "by": "rider"},
            source="rider_portal",
        )
    transaction.on_commit(lambda: NotificationService().notify_rider_cancelled(trip_request))

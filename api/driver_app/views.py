"""
The driver app (ADR 0029): a phone-sized web app at /driver/. Today's
manifest, one page per trip with directions and a single big button
for the next step; every step is stamped with server time and the
phone's GPS position (trips.events).
"""

import time
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from accounts.onboarding import mark_password_changed, must_change_password
from audit.services import record_change
from drivers.models import DriverStatus
from trips.events import NEEDS_REASON, TripEventError, next_steps, record_trip_event
from trips.models import Trip, TripEventReason, TripEventType, TripStatus

from .auth import is_locked_out, record_failure

# Phones get left on dashboards and passed around; trip details are PHI.
IDLE_TIMEOUT_SECONDS = 60 * 60
SESSION_SEEN_KEY = "driver_last_seen"


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def _maps_url(address: str) -> str:
    # Opens Google Maps (app if installed) on Android and iPhone alike.
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(address)}"


def driver_required(view=None, *, allow_temporary_password=False):
    def decorator(view_func):
        @wraps(view_func)
        @never_cache
        def wrapper(request, *args, **kwargs):
            user = request.user
            driver = getattr(user, "driver_profile", None) if user.is_authenticated else None
            if driver is None:
                return redirect("driver_app:login")
            if driver.status != DriverStatus.ACTIVE:
                logout(request)
                messages.error(request, "Your driver account isn't active. Please call dispatch.")
                return redirect("driver_app:login")
            now = time.time()
            last_seen = request.session.get(SESSION_SEEN_KEY)
            if last_seen and now - last_seen > IDLE_TIMEOUT_SECONDS:
                logout(request)
                messages.info(request, "You were signed out after an hour without activity.")
                return redirect("driver_app:login")
            request.session[SESSION_SEEN_KEY] = now
            if not allow_temporary_password and must_change_password(user):
                return redirect("driver_app:password")
            request.driver = driver
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator(view) if view else decorator


@never_cache
def login_view(request):
    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        password = request.POST.get("password", "")
        if is_locked_out(identifier):
            messages.error(
                request,
                "Too many attempts. Wait 15 minutes, or call dispatch to reset your password.",
            )
        else:
            user = authenticate(request, identifier=identifier, password=password)
            if user is not None:
                login(request, user, backend="driver_app.auth.DriverLoginBackend")
                request.session[SESSION_SEEN_KEY] = time.time()
                record_change(
                    actor=user,
                    action="driver_logged_in",
                    resource_type="Driver",
                    resource_id=user.driver_profile.pk,
                    source="driver_app",
                )
                return redirect("driver_app:home")
            record_failure(identifier)
            messages.error(request, "That email/phone or password isn't right.")
        return render(request, "driver_app/login.html", {"identifier": identifier}, status=200)
    return render(request, "driver_app/login.html", {})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("driver_app:login")


@driver_required(allow_temporary_password=True)
def password_view(request):
    first_time = must_change_password(request.user)
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        mark_password_changed(user)
        messages.success(request, "Password saved.")
        return redirect("driver_app:home")
    return render(request, "driver_app/password.html", {"form": form, "first_time": first_time})


def _visible_trips(driver):
    """Today's trips (Texas day) plus anything still in progress."""
    today = timezone.now().astimezone(_zone()).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=_zone())
    return (
        Trip.objects.filter(driver=driver)
        .filter(
            Q(scheduled_pickup_datetime__gte=start, scheduled_pickup_datetime__lt=start + timedelta(days=1))
            | Q(status=TripStatus.IN_PROGRESS)
        )
        .select_related("passenger", "vehicle")
        .prefetch_related("events")
        .order_by("scheduled_pickup_datetime")
    ), today


@driver_required
def home(request):
    trips, today = _visible_trips(request.driver)
    trips = list(trips)
    record_change(
        actor=request.user,
        action="viewed",
        resource_type="DriverManifest",
        resource_id=today.isoformat(),
        source="driver_app",
    )
    for trip in trips:
        events = list(trip.events.all())
        trip.last_event = events[-1] if events else None
    open_trips = [t for t in trips if t.status in (TripStatus.SCHEDULED, TripStatus.IN_PROGRESS)]
    return render(
        request,
        "driver_app/home.html",
        {
            "driver": request.driver,
            "today": today,
            "zone": settings.OPERATIONS_TIME_ZONE,
            "trips": trips,
            "open_count": len(open_trips),
        },
    )


@driver_required
def trip_detail(request, trip_id):
    trips, _ = _visible_trips(request.driver)
    trip = trips.filter(pk=trip_id).first()
    if trip is None:
        raise Http404
    record_change(
        actor=request.user,
        action="viewed",
        resource_type="Trip",
        resource_id=trip.pk,
        source="driver_app",
    )
    steps = next_steps(trip)
    return render(
        request,
        "driver_app/trip.html",
        {
            "trip": trip,
            "zone": settings.OPERATIONS_TIME_ZONE,
            "events": list(trip.events.all()),
            "forward_steps": [s for s in steps if s not in NEEDS_REASON],
            "end_steps": [s for s in steps if s in NEEDS_REASON],
            "reasons": TripEventReason.choices,
            "pickup_maps_url": _maps_url(trip.pickup_address),
            "dropoff_maps_url": _maps_url(trip.dropoff_address),
            # Before pickup the driver heads to the pickup; after, to the drop-off.
            "heading_to_dropoff": any(e.event_type == TripEventType.PICKED_UP for e in trip.events.all()),
        },
    )


@require_POST
@driver_required
def record_step(request, trip_id):
    trips, _ = _visible_trips(request.driver)
    trip = trips.filter(pk=trip_id).first()
    if trip is None:
        raise Http404
    try:
        event = record_trip_event(
            trip,
            request.POST.get("event_type", ""),
            user=request.user,
            latitude=request.POST.get("latitude"),
            longitude=request.POST.get("longitude"),
            accuracy=request.POST.get("accuracy"),
            reason=request.POST.get("reason", ""),
            note=request.POST.get("note", ""),
        )
    except (TripEventError, ValueError) as exc:
        messages.error(request, str(exc) if isinstance(exc, TripEventError) else "Unknown step.")
        return redirect("driver_app:trip", trip_id=trip.pk)
    label = event.get_event_type_display()
    if not event.has_location:
        messages.warning(request, f"{label} saved — but without your location. Turn on location for this site.")
    else:
        messages.success(request, f"{label} saved.")
    if event.event_type in (TripEventType.DROPPED_OFF, TripEventType.NO_SHOW, TripEventType.CANCELLED_AT_DOOR):
        return redirect(reverse("driver_app:home"))
    return redirect("driver_app:trip", trip_id=trip.pk)

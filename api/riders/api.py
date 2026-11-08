"""
JSON API behind the website's "My trips" pages (ADR 0031), under
/api/v1/rider/. The website proxies it at its own address, so the
session cookie belongs to angelcaretransit.com and is never cross-site.

Session auth with CSRF on every write, including sign-in. Rider
sessions expire after RIDER_IDLE_SECONDS without a request -- shared
family tablets and facility workstations are the norm, not the
exception.
"""

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.services import record_change
from verification.services import issue_token
from transportation.addresses import PARTS, AddressPartsSerializer
from transportation.models import ReturnRide, TripRequestStatus
from trips.models import TripStatus

from . import services
from .auth import is_locked_out, record_failure
from .models import RiderAccount, SavedAddress

RIDER_IDLE_SECONDS = 15 * 60


class IsRider(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        account = getattr(user, "rider_account", None) if user.is_authenticated else None
        if account is None or account.verified_at is None or not user.is_active:
            return False
        # Sliding idle timeout: every request pushes expiry back.
        request.session.set_expiry(RIDER_IDLE_SECONDS)
        return True


class RiderView(APIView):
    permission_classes = [IsRider]

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    @property
    def account(self) -> RiderAccount:
        return self.request.user.rider_account


@method_decorator(csrf_protect, name="dispatch")
class PublicRiderView(APIView):
    """Anonymous endpoints. CSRF enforced anyway (no login CSRF)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    # Own, roomier limit: requests arrive through the website's proxy, so
    # the shared anonymous limit could end up shared by every rider.
    # Password guessing is stopped per email by the lockout instead.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rider_auth"


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """GET first: sets the csrftoken cookie the website sends back as X-CSRFToken."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        return Response({"ok": True})


def _account_payload(account: RiderAccount) -> dict:
    return {
        "email": account.email,
        "full_name": account.full_name,
        "phone": account.phone,
        "mobility_notes": account.mobility_notes,
        "addresses": [
            {"id": str(a.id), "label": a.label, "address": a.address, **{p: getattr(a, p) for p in PARTS}}
            for a in account.addresses.all()
        ],
        "idle_timeout_seconds": RIDER_IDLE_SECONDS,
    }


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class LoginView(PublicRiderView):
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email, password = data.validated_data["email"], data.validated_data["password"]
        if is_locked_out(email):
            return Response(
                {"detail": "Too many attempts. Wait 15 minutes or reset your password."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        user = authenticate(request, rider_email=email, password=password)
        if user is None:
            record_failure(email)
            return Response(
                {"detail": "That email or password isn't right."}, status=status.HTTP_400_BAD_REQUEST
            )
        login(request._request, user, backend="riders.auth.RiderLoginBackend")
        request.session.set_expiry(RIDER_IDLE_SECONDS)
        record_change(
            actor=user,
            action="rider_logged_in",
            resource_type="RiderAccount",
            resource_id=user.rider_account.pk,
            source="rider_portal",
        )
        return Response(_account_payload(user.rider_account))


class LogoutView(PublicRiderView):
    def post(self, request):
        logout(request._request)
        return Response({"ok": True})


class ForgotPasswordView(PublicRiderView):
    def post(self, request):
        email = serializers.EmailField().run_validation(request.data.get("email"))
        services.request_password_reset(email)
        # Same answer whether or not the email has an account.
        return Response({"ok": True})


class SetPasswordView(PublicRiderView):
    def post(self, request):
        try:
            user = services.set_password_from_link(
                str(request.data.get("uid", "")),
                str(request.data.get("token", "")),
                str(request.data.get("password", "")),
            )
        except services.RiderAccountError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DjangoValidationError as exc:
            return Response({"password": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        login(request._request, user, backend="riders.auth.RiderLoginBackend")
        request.session.set_expiry(RIDER_IDLE_SECONDS)
        return Response(_account_payload(user.rider_account))


class ProfileSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    phone = serializers.RegexField(r"^[0-9()+\-.\s]{7,20}$", required=False, allow_blank=True)
    mobility_notes = serializers.CharField(required=False, allow_blank=True)


class MeView(RiderView):
    def get(self, request):
        return Response(_account_payload(self.account))

    def patch(self, request):
        data = ProfileSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        account = self.account
        for field, value in data.validated_data.items():
            setattr(account, field, value.strip() if isinstance(value, str) else value)
        account.save()
        return Response(_account_payload(account))


class AddressSerializer(AddressPartsSerializer):
    label = serializers.CharField(max_length=60, required=False, allow_blank=True)

    def validate(self, attrs):
        label = attrs.pop("label", "").strip()
        return {**super().validate(attrs), "label": label}


class AddressesView(RiderView):
    def post(self, request):
        data = AddressSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if self.account.addresses.count() >= SavedAddress.MAX_PER_ACCOUNT:
            return Response(
                {"detail": f"You can save up to {SavedAddress.MAX_PER_ACCOUNT} addresses."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        SavedAddress.objects.create(account=self.account, **data.validated_data)
        return Response(_account_payload(self.account), status=status.HTTP_201_CREATED)


class AddressDetailView(RiderView):
    def delete(self, request, address_id):
        from trash.models import DeletedFrom
        from trash.services import send

        address = self.account.addresses.filter(pk=address_id).first()
        if address is not None:  # kept in the staff trash, gone from the rider's list (ADR 0046)
            send(address, user=request.user, deleted_from=DeletedFrom.RIDER)
        return Response(_account_payload(self.account))


def _parts(trip_request, side: str) -> dict | None:
    if not trip_request.has_address_parts(side):
        return None
    return {part: getattr(trip_request, f"{side}_{part}") for part in PARTS}


def _return_payload(trip_request) -> dict | None:
    """The ride home booked with this request (ADR 0032), as the rider should see it."""
    if trip_request.return_ride == ReturnRide.NONE:
        return None
    back = services.return_trip_of(trip_request)
    if back is None:
        pickup = trip_request.return_datetime
    elif back.status == TripStatus.CANCELLED and trip_request.status != TripRequestStatus.CANCELLED:
        return None  # staff dropped the ride home: don't promise it
    elif back.will_call or back.driver_waits:
        pickup = None  # only an estimate on our side
    else:
        pickup = back.scheduled_pickup_datetime
    return {
        "kind": trip_request.return_ride,
        "text": trip_request.get_return_ride_display(),
        "pickup": pickup.isoformat() if pickup else None,
    }


def _trip_payload(trip_request) -> dict:
    key, wording = services.rider_status(trip_request)
    trip = services.trip_of(trip_request)
    assigned = trip is not None and trip.driver_id and trip.vehicle_id
    return {
        "id": str(trip_request.id),
        "reference": f"#{str(trip_request.id)[:8].upper()}",
        "status": key,
        "status_text": wording,
        "service_type": trip_request.service_type,
        "service_type_text": trip_request.get_service_type_display(),
        "requested_pickup": trip_request.requested_datetime.isoformat(),
        "confirmed_pickup": trip.scheduled_pickup_datetime.isoformat() if trip else None,
        "pickup_address": trip.pickup_address if trip else trip_request.pickup_address,
        "dropoff_address": trip.dropoff_address if trip else trip_request.dropoff_address,
        # The parts as booked, for "Book again" (null for older requests).
        "pickup": _parts(trip_request, "pickup"),
        "dropoff": _parts(trip_request, "dropoff"),
        "return_ride": _return_payload(trip_request),
        "mobility_notes": trip_request.mobility_notes,
        # First name only, plus the vehicle -- enough to recognise the ride.
        "driver_first_name": trip.driver.legal_name.split()[0] if assigned else None,
        "vehicle": f"{trip.vehicle.make} {trip.vehicle.model}" if assigned else None,
        "can_cancel": services.can_cancel(trip_request),
    }


class BookingTokenView(RiderView):
    """
    Signed-in riders proved their email when they set a password, so the
    booking form gets its email token from here instead of a code
    (ADR 0033). Only ever for the account's own email.
    """

    def post(self, request):
        return Response({"email": self.account.email, "token": issue_token(self.account.email)})


class TripsView(RiderView):
    def get(self, request):
        requests = list(services.requests_for(self.account))
        record_change(
            actor=request.user,
            action="viewed_list",
            resource_type="RiderTrips",
            resource_id=str(self.account.pk),
            source="rider_portal",
        )
        return Response({"trips": [_trip_payload(r) for r in requests]})


class CancelTripView(RiderView):
    def post(self, request, request_id):
        trip_request = services.requests_for(self.account).filter(pk=request_id).first()
        if trip_request is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.cancel_request(self.account, trip_request)
        except services.RiderAccountError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        trip_request.refresh_from_db()
        return Response(_trip_payload(trip_request))


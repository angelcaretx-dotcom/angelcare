"""
JSON API behind the website's facility portal (ADR 0042), under
/api/v1/facility/. Like the rider pages (ADR 0031) it is called through
the website's own /api/ proxy, so the session cookie is first-party.

Session auth, CSRF on every write including both sign-in steps, and a
15-minute idle timeout: facility workstations are shared.
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
from riders.api import _trip_payload
from riders.services import trip_of
from transportation.serializers import TripRequestCreateSerializer
from verification.services import VerificationError

from . import services
from .auth import is_locked_out, record_failure
from .models import FacilityUser

IDLE_SECONDS = 15 * 60


class IsFacilityUser(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        portal_user = getattr(user, "facility_user", None) if user.is_authenticated else None
        if portal_user is None or portal_user.verified_at is None or not user.is_active:
            return False
        request.session.set_expiry(IDLE_SECONDS)  # sliding idle timeout
        return True


class PortalView(APIView):
    permission_classes = [IsFacilityUser]

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    @property
    def portal_user(self) -> FacilityUser:
        return self.request.user.facility_user


@method_decorator(csrf_protect, name="dispatch")
class PublicPortalView(APIView):
    """Anonymous endpoints, CSRF enforced anyway (no login CSRF)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "facility_auth"


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        return Response({"ok": True})


def _me(portal_user: FacilityUser) -> dict:
    facility = portal_user.facility
    return {
        "email": portal_user.email,
        "full_name": portal_user.full_name,
        "facility": {"name": facility.name, "address": facility.address, "phone": facility.phone,
                     "street": facility.street, "unit": facility.unit, "city": facility.city,
                     "state": facility.state, "zip": facility.zip},
        "idle_timeout_seconds": IDLE_SECONDS,
    }


def _signed_in(request, user) -> Response:
    login(request._request, user, backend="facilities.auth.FacilityLoginBackend")
    request.session.set_expiry(IDLE_SECONDS)
    record_change(actor=user, action="facility_logged_in", resource_type="FacilityUser",
                  resource_id=user.facility_user.pk, source=services.SOURCE)
    return Response(_me(user.facility_user))


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class LoginView(PublicPortalView):
    """Step one: the password. A right one emails a code; nothing is signed in yet."""

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email, password = data.validated_data["email"], data.validated_data["password"]
        if is_locked_out(email):
            return Response({"detail": "Too many attempts. Wait 15 minutes or reset your password."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        user = authenticate(request, facility_email=email, password=password)
        if user is None:
            record_failure(email)
            return Response({"detail": "That email or password isn't right."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            services.start_sign_in(request.session, user)
        except VerificationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response({"code_sent": True})


class CodeSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\s*\d{6}\s*$", error_messages={"invalid": "Enter the 6-digit code."})


class LoginCodeView(PublicPortalView):
    """Step two: the code we just emailed."""

    def post(self, request):
        data = CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            user = services.finish_sign_in(request.session, data.validated_data["code"])
        except services.PortalError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return _signed_in(request, user)


class LogoutView(PublicPortalView):
    def post(self, request):
        logout(request._request)
        return Response({"ok": True})


class ForgotPasswordView(PublicPortalView):
    def post(self, request):
        services.request_password_reset(serializers.EmailField().run_validation(request.data.get("email")))
        return Response({"ok": True})  # same answer either way


class SetPasswordView(PublicPortalView):
    """The emailed link already proves the inbox, so a new password signs straight in."""

    def post(self, request):
        try:
            user = services.set_password_from_link(str(request.data.get("uid", "")),
                                                   str(request.data.get("token", "")),
                                                   str(request.data.get("password", "")))
        except services.PortalError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DjangoValidationError as exc:
            return Response({"password": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return _signed_in(request, user)


class MeView(PortalView):
    def get(self, request):
        return Response(_me(self.portal_user))


def _request_payload(trip_request) -> dict:
    trip = trip_of(trip_request)
    return {
        **_trip_payload(trip_request),
        "rider_name": trip.passenger.legal_name if trip and trip.passenger_id else trip_request.full_name,
        "booked_by": trip_request.facility_user.full_name if trip_request.facility_user_id else "",
    }


def _office_trip_payload(trip) -> dict:
    key, wording = services.TRIP_STATUS_WORDS[trip.status]
    assigned = trip.driver_id and trip.vehicle_id
    return {
        "id": str(trip.id),
        "reference": f"#{str(trip.id)[:8].upper()}",
        "status": key,
        "status_text": wording,
        "service_type": trip.service_type,
        "service_type_text": trip.get_service_type_display(),
        "requested_pickup": trip.scheduled_pickup_datetime.isoformat(),
        "confirmed_pickup": trip.scheduled_pickup_datetime.isoformat(),
        "pickup_address": trip.pickup_address,
        "dropoff_address": trip.dropoff_address,
        "pickup": None,
        "dropoff": None,
        "return_ride": None,
        "mobility_notes": "",
        "driver_first_name": trip.driver.legal_name.split()[0] if assigned else None,
        "vehicle": f"{trip.vehicle.make} {trip.vehicle.model}" if assigned else None,
        "can_cancel": False,  # booked by the office: changes go through the office
        "rider_name": trip.passenger.legal_name if trip.passenger_id else "",
        "booked_by": "AngelCare office",
    }


class TripsView(PortalView):
    def get(self, request):
        facility = self.portal_user.facility
        rows = [_request_payload(r) for r in services.requests_for(facility)]
        rows += [_office_trip_payload(t) for t in services.trips_for(facility)]
        rows.sort(key=lambda row: row["confirmed_pickup"] or row["requested_pickup"], reverse=True)
        record_change(actor=request.user, action="viewed_list", resource_type="FacilityTrips",
                      resource_id=str(facility.pk), source=services.SOURCE)
        return Response({"trips": rows})


class BookingSerializer(TripRequestCreateSerializer):
    """The website booking form's checks, minus the email code: the portal user is signed in."""

    class Meta(TripRequestCreateSerializer.Meta):
        fields = [f for f in TripRequestCreateSerializer.Meta.fields if f not in ("email", "booked_for",
                                                                                  "verification_token")]

    verification_token = None

    def email_is_proven(self, attrs) -> bool:
        return True


class BookView(PortalView):
    def post(self, request):
        data = BookingSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        trip_request = services.book(self.portal_user, data.validated_data)
        trip_request = services.requests_for(self.portal_user.facility).get(pk=trip_request.pk)
        return Response(_request_payload(trip_request), status=status.HTTP_201_CREATED)


class CancelView(PortalView):
    def post(self, request, request_id):
        trip_request = services.requests_for(self.portal_user.facility).filter(pk=request_id).first()
        if trip_request is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.cancel(self.portal_user, trip_request)
        except services.PortalError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_request_payload(services.requests_for(self.portal_user.facility).get(pk=request_id)))

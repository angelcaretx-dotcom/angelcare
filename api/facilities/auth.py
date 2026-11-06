from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from audit import lockout

from .models import FacilityUser


def lockout_key(email: str) -> str:
    return f"facility:{(email or '').strip().lower()}"


def is_locked_out(email: str) -> bool:
    return lockout.is_locked_out("facility_login_failed", lockout_key(email))


def record_failure(email: str) -> None:
    lockout.record_failure("facility_login_failed", "FacilityLogin", lockout_key(email), "facility_portal")


class FacilityLoginBackend(BaseBackend):
    """
    Facility portal sign-in, step one (ADR 0042): email + password, for
    accounts whose owner has set a password. Only answers calls made with
    `facility_email=`. Step two is the emailed code (services.finish_sign_in).
    """

    def authenticate(self, request, facility_email=None, password=None, **kwargs):
        if facility_email is None or password is None:
            return None
        portal_user = (
            FacilityUser.objects.filter(email=facility_email.strip().lower(), verified_at__isnull=False)
            .select_related("user")
            .first()
        )
        if portal_user is None:
            get_user_model()().set_password(password)  # same cost either way
            return None
        user = portal_user.user
        if user.is_active and user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        try:
            return get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            return None

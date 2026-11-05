from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from audit import lockout

from .models import RiderAccount


def lockout_key(email: str) -> str:
    return f"rider:{(email or '').strip().lower()}"


def is_locked_out(email: str) -> bool:
    return lockout.is_locked_out("rider_login_failed", lockout_key(email))


def record_failure(email: str) -> None:
    lockout.record_failure("rider_login_failed", "RiderLogin", lockout_key(email), "rider_portal")


class RiderLoginBackend(BaseBackend):
    """
    Website "My trips" sign-in (ADR 0031): email + password, only for
    accounts whose owner has verified the email by setting a password.
    Only answers calls made with `rider_email=`.
    """

    def authenticate(self, request, rider_email=None, password=None, **kwargs):
        if rider_email is None or password is None:
            return None
        account = (
            RiderAccount.objects.filter(email=rider_email.strip().lower(), verified_at__isnull=False)
            .select_related("user")
            .first()
        )
        if account is None:
            get_user_model()().set_password(password)  # same cost either way
            return None
        user = account.user
        if user.is_active and user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        try:
            return get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            return None

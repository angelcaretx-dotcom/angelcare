"""
Driver sign-in (ADR 0029): a driver types the email or phone number on
their Driver record, plus their password. No usernames, and no
authenticator app -- instead repeated failures lock the identifier for
a while, and drivers only ever see their own trips.
"""

import re
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from audit import lockout
from audit.lockout import MAX_FAILURES  # noqa: F401 - re-exported for tests
from drivers.models import Driver, DriverStatus


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def lockout_key(identifier: str) -> str:
    identifier = (identifier or "").strip()
    if "@" in identifier:
        return f"email:{identifier.lower()}"
    return f"phone:{normalize_phone(identifier)}"


def find_driver(identifier: str, *, with_login=True) -> Driver | None:
    """The one driver this email/phone belongs to, or None (also if ambiguous)."""
    identifier = (identifier or "").strip()
    drivers = Driver.objects.all()
    if with_login:
        drivers = drivers.filter(user__isnull=False)
    if "@" in identifier:
        matches = list(drivers.filter(email__iexact=identifier))
    else:
        digits = normalize_phone(identifier)
        if len(digits) < 7:
            return None
        matches = [d for d in drivers if normalize_phone(d.phone) == digits]
    return matches[0] if len(matches) == 1 else None


def is_locked_out(identifier: str) -> bool:
    return lockout.is_locked_out("driver_login_failed", lockout_key(identifier))


def record_failure(identifier: str) -> None:
    lockout.record_failure("driver_login_failed", "DriverLogin", lockout_key(identifier), "driver_app")


class DriverLoginBackend(BaseBackend):
    """
    Only used when called with `identifier=` (the driver app's login);
    the admin's username/password login never reaches it.
    """

    def authenticate(self, request, identifier=None, password=None, **kwargs):
        if identifier is None or password is None:
            return None
        driver = find_driver(identifier)
        if driver is None or driver.status != DriverStatus.ACTIVE:
            # Same cost as a real check, so timing doesn't reveal which
            # emails/phones belong to drivers.
            get_user_model()().set_password(password)
            return None
        user = driver.user
        if user.is_active and user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        try:
            return get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            return None

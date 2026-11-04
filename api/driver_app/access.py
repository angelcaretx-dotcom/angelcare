"""
Giving a driver an app login, from the Driver admin page (ADR 0029).
Same handover model as staff onboarding (ADR 0025): a temporary
password shown once on a printable sheet, never emailed, and replaced
by the driver at first sign-in.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from accounts.models import StaffOnboarding
from accounts.onboarding import generate_temporary_password
from audit.services import record_change
from drivers.models import Driver, DriverStatus

from .auth import find_driver


class DriverAccessError(Exception):
    pass


@transaction.atomic
def setup_driver_login(driver: Driver, *, actor) -> str:
    """Creates the login, or resets its password. Returns the temporary password."""
    if driver.status != DriverStatus.ACTIVE:
        raise DriverAccessError("Only an Active driver can be given an app login.")
    for identifier in filter(None, [driver.email, driver.phone]):
        other = find_driver(identifier, with_login=False)
        if other is None or other.pk != driver.pk:
            raise DriverAccessError(
                f"Another driver has the same {'email' if '@' in identifier else 'phone number'} "
                f"({identifier}). Each driver needs their own, so sign-in knows who it is."
            )

    password = generate_temporary_password()
    user = driver.user
    created = user is None
    if created:
        user = get_user_model()(
            username=f"driver-{driver.pk}",
            email=driver.email,
            first_name=driver.legal_name[:150],
            is_staff=False,
        )
    validate_password(password, user)
    user.set_password(password)
    user.is_active = True
    user.save()
    if created:
        driver.user = user
        driver.save(update_fields=["user", "updated_at"])
    StaffOnboarding.objects.update_or_create(
        user=user, defaults={"must_change_password": True, "created_by": actor}
    )
    record_change(
        actor=actor,
        action="driver_login_created" if created else "driver_login_reset",
        resource_type="Driver",
        resource_id=driver.pk,
        source="admin",
    )
    return password

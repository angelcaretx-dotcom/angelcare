"""
Adding a staff member from the browser (ADR 0025), so a superuser never
needs server access to bring on a dispatcher or administrator.

One step creates everything the new person needs for their first login:
a normal staff account in a role Group (ADR 0005), a temporary password,
and single-use recovery codes standing in for an authenticator they
don't have yet (ADR 0024). Both secrets are shown once, on a printable
handover sheet, and are never emailed. accounts.middleware then makes
the new person replace the password and set up their own authenticator
before they can use anything else.
"""

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone

from audit.services import record_change

from . import mfa
from .models import StaffOnboarding

# Only roles that exist (ADR 0005). Superusers are still made by hand.
ROLES = ["Dispatcher", "Administrator"]

# No 0/o, 1/l/i: the password is read off paper and typed in.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class OnboardingError(Exception):
    pass


def generate_temporary_password() -> str:
    """e.g. 'k7mq-x3pa-9frt-2bnw': 16 random characters, easy to type."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


@transaction.atomic
def create_staff_member(*, email, first_name, last_name, role, actor):
    """Returns (user, temporary_password, recovery_codes)."""
    from .access import can_manage_access  # access imports ROLES from here

    if not can_manage_access(actor):
        raise OnboardingError("Only an administrator can add staff members.")
    if role not in ROLES:
        raise OnboardingError(f"Unknown role {role!r}.")
    email = email.strip().lower()
    User = get_user_model()
    if User.objects.filter(username__iexact=email).exists() or User.objects.filter(
        email__iexact=email
    ).exists():
        raise OnboardingError("There's already an account with that email address.")

    user = User(
        username=email,
        email=email,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        is_staff=True,
    )
    password = generate_temporary_password()
    validate_password(password, user)
    user.set_password(password)
    user.save()
    user.groups.add(Group.objects.get(name=role))
    StaffOnboarding.objects.create(user=user, created_by=actor)

    record_change(
        actor=actor,
        action="staff_member_added",
        resource_type="auth.user",
        resource_id=user.pk,
        after={"username": email, "role": role},
        source="admin",
    )
    codes = mfa.generate_backup_codes(user, actor=actor, new_account=True)
    return user, password, codes


def must_change_password(user) -> bool:
    return StaffOnboarding.objects.filter(user=user, must_change_password=True).exists()


def mark_password_changed(user) -> None:
    updated = StaffOnboarding.objects.filter(user=user, must_change_password=True).update(
        must_change_password=False, password_changed_at=timezone.now()
    )
    if updated:
        record_change(
            actor=user,
            action="staff_temporary_password_replaced",
            resource_type="auth.user",
            resource_id=user.pk,
            source="admin",
        )

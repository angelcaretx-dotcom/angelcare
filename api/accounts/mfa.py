"""
Staff MFA recovery and self-service (ADR 0024), on top of ADR 0014's
TOTP enforcement:

- backup codes: single-use codes (django-otp's otp_static plugin) for
  when a staff member's phone is lost, broken, or out of battery;
- adding/removing their own authenticator apps, so a new phone doesn't
  need someone with server access running `bootstrap_totp`;
- superuser-issued recovery codes for a locked-out colleague who has
  no backup codes left -- the same codes, generated on their behalf
  and handed over in person, after which they log in and enroll a new
  authenticator themselves.

Every change is written to AuditLog. Codes and secrets never are.
"""

import io

import qrcode
import qrcode.image.svg
from django.db import transaction
from django_otp import devices_for_user
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.services import record_change

BACKUP_CODE_COUNT = 10
BACKUP_DEVICE_NAME = "backup codes"


class MfaError(Exception):
    """A refused MFA change, with a message safe to show the user."""


def normalize_backup_code(code: str) -> str:
    """Backup codes are lowercase base32; accept them as people retype them."""
    return "".join(code.split()).replace("-", "").lower()


def backup_codes_remaining(user) -> int:
    return StaticToken.objects.filter(
        device__user=user, device__confirmed=True
    ).count()


def authenticators(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).order_by("created_at")


@transaction.atomic
def generate_backup_codes(user, *, actor) -> list[str]:
    """
    Replace `user`'s backup codes with BACKUP_CODE_COUNT fresh ones and
    return them -- the only time they're ever available in plain text.
    `actor` is who asked: the user themselves, or a superuser issuing
    recovery codes for them.
    """
    # Only superusers manage accounts (the Administrator role has no
    # user permissions), so only they may issue codes for someone else.
    if actor.pk != user.pk and not actor.is_superuser:
        raise MfaError("Only a superuser can issue recovery codes for someone else.")

    device, _ = StaticDevice.objects.get_or_create(
        user=user, name=BACKUP_DEVICE_NAME, defaults={"confirmed": True}
    )
    device.confirmed = True
    # Fresh codes also clear any lockout from earlier failed attempts.
    device.throttling_failure_count = 0
    device.throttling_failure_timestamp = None
    device.save()
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(BACKUP_CODE_COUNT)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=c) for c in codes)

    record_change(
        actor=actor,
        action="mfa_backup_codes_generated" if actor.pk == user.pk else "mfa_recovery_codes_issued",
        resource_type="auth.user",
        resource_id=user.pk,
        after={"username": user.get_username(), "codes": BACKUP_CODE_COUNT},
        source="admin",
    )
    return codes


@transaction.atomic
def start_authenticator_enrollment(user) -> TOTPDevice:
    """
    A new, *unconfirmed* TOTP device to show as a QR code. Unconfirmed
    devices are ignored at login (devices_for_user(confirmed=True)), so
    abandoning enrollment halfway leaves nothing usable behind.
    """
    TOTPDevice.objects.filter(user=user, confirmed=False).delete()
    return TOTPDevice.objects.create(user=user, name="pending", confirmed=False)


def pending_authenticator(user) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=user, confirmed=False).order_by("-created_at").first()


@transaction.atomic
def confirm_authenticator(user, *, code: str, name: str) -> TOTPDevice:
    """Activate the pending device once the user proves it shows the right code."""
    device = pending_authenticator(user)
    if device is None:
        raise MfaError("That setup expired. Please start again.")
    if not device.verify_token(code.strip()):
        raise MfaError("That code didn't match. Check the app and try the current code.")
    device.name = name.strip()[:64] or "Authenticator"
    device.confirmed = True
    device.save()
    record_change(
        actor=user,
        action="mfa_authenticator_added",
        resource_type="auth.user",
        resource_id=user.pk,
        after={"device": device.name},
        source="admin",
    )
    return device


@transaction.atomic
def remove_authenticator(user, device_id) -> None:
    device = authenticators(user).filter(pk=device_id).first()
    if device is None:
        raise MfaError("That authenticator doesn't exist.")
    if authenticators(user).count() == 1:
        raise MfaError(
            "You can't remove your only authenticator. Add the new one first, "
            "then remove this one."
        )
    name = device.name
    device.delete()
    record_change(
        actor=user,
        action="mfa_authenticator_removed",
        resource_type="auth.user",
        resource_id=user.pk,
        before={"device": name},
        source="admin",
    )


def has_usable_mfa(user) -> bool:
    return any(True for _ in devices_for_user(user, confirmed=True))


def qr_svg(data: str) -> str:
    """Inline SVG QR code (no image library or external service needed)."""
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")

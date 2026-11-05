"""
Email codes for the website forms (ADR 0033).

1. ``start``: email a 6-digit code (10 minutes, 5 tries). Capped per
   email address so nobody can use us to flood someone's inbox.
2. ``check``: the right code earns a token.
3. ``token_is_valid``: the booking and contact forms accept a token for
   the same email address for TOKEN_TTL. That way a facility booking
   several rides, or anyone fixing a form error, confirms only once.

Signed-in riders already proved their email when they set a password,
so ``issue_token`` gives them one without a code.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import EmailVerification, Purpose

CODE_TTL = timedelta(minutes=10)
TOKEN_TTL = timedelta(hours=2)
MAX_ATTEMPTS = 5
# Codes per email address: a burst limit and a daily limit.
MAX_CODES_PER_15_MINUTES = 3
MAX_CODES_PER_DAY = 10


class VerificationError(Exception):
    """Shown to the person as is."""


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def _code_hash(verification_id, code: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{verification_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start(email: str, purpose: str) -> EmailVerification:
    """Email a fresh code. Raises VerificationError when this address has had too many."""
    email = _normalize(email)
    now = timezone.now()
    recent = EmailVerification.objects.filter(email=email, code_hash__gt="")
    if recent.filter(created_at__gte=now - timedelta(minutes=15)).count() >= MAX_CODES_PER_15_MINUTES:
        raise VerificationError(
            "We've sent several codes to this address. Use the latest one, or wait 15 minutes."
        )
    if recent.filter(created_at__gte=now - timedelta(days=1)).count() >= MAX_CODES_PER_DAY:
        raise VerificationError("Too many codes for this address today. Please call us instead.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    verification = EmailVerification(email=email, purpose=purpose, expires_at=now + CODE_TTL)
    verification.code_hash = _code_hash(verification.id, code)
    verification.save()

    from notifications.services import NotificationService

    NotificationService().send_verification_code(verification, code)
    return verification


def check(verification_id, code: str) -> tuple[str, EmailVerification]:
    """The token for a correct code; VerificationError otherwise."""
    # The failed attempt is counted before raising, outside the
    # transaction, so a rollback can't undo the count.
    with transaction.atomic():
        verification = (
            EmailVerification.objects.select_for_update().filter(pk=verification_id, code_hash__gt="").first()
        )
        problem = None
        if verification is None:
            problem = "That code isn't right. Check the latest email from us."
        elif verification.verified_at is not None:
            problem = "That code has already been used. Ask for a new one."
        elif verification.expires_at <= timezone.now() or verification.attempts >= MAX_ATTEMPTS:
            problem = "That code has expired. Ask for a new one."
        else:
            verification.attempts += 1
            entered = _code_hash(verification.id, (code or "").strip())
            if hmac.compare_digest(verification.code_hash, entered):
                return _grant(verification), verification
            verification.save(update_fields=["attempts"])
            problem = "That code isn't right. Check the latest email from us."
    raise VerificationError(problem)


def _grant(verification: EmailVerification) -> str:
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    verification.verified_at = now
    verification.token_hash = _token_hash(token)
    verification.token_expires_at = now + TOKEN_TTL
    verification.save()
    return token


def issue_token(email: str, purpose: str = Purpose.BOOKING) -> str:
    """A token without a code, for an email already proven another way (a signed-in rider)."""
    verification = EmailVerification(email=_normalize(email), purpose=purpose, expires_at=timezone.now())
    return _grant(verification)


def token_is_valid(token: str, email: str) -> bool:
    if not token:
        return False
    return EmailVerification.objects.filter(
        token_hash=_token_hash(token),
        email=_normalize(email),
        token_expires_at__gt=timezone.now(),
    ).exists()

"""
Sign-in lockout counted in the audit log (ADR 0029, 0031): N failures
for one email/phone within the window block it for the rest of the
window. Kept in the database because in-memory counters don't survive
between serverless requests.
"""

from datetime import timedelta

from django.utils import timezone

from .models import AuditLog
from .services import record_change

MAX_FAILURES = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


def is_locked_out(action: str, key: str) -> bool:
    return (
        AuditLog.objects.filter(
            action=action,
            resource_id=key,
            created_at__gte=timezone.now() - LOCKOUT_WINDOW,
        ).count()
        >= MAX_FAILURES
    )


def record_failure(action: str, resource_type: str, key: str, source: str) -> None:
    record_change(actor=None, action=action, resource_type=resource_type, resource_id=key, source=source)

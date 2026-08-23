from typing import Any

from .models import AuditLog


def record_change(
    *,
    actor,
    action: str,
    resource_type: str,
    resource_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    source: str,
) -> AuditLog:
    """
    Record an audit entry. `before`/`after` should be small, JSON-safe
    dicts of just the fields that changed (not a full model dump) --
    keeps entries readable and avoids serialization surprises with
    non-JSON-safe field types.

    Unlike NotificationService, this is allowed to raise: a failure to
    record an audit entry for a sensitive change is itself something
    the caller needs to know about, not something to silently swallow.
    """
    return AuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        before=before,
        after=after,
        source=source,
    )

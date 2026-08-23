import uuid

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """
    General-purpose audit trail for important state changes across any
    domain -- not tied to Django admin specifically, so it works the
    same whether the change came from /admin/ or a future API-driven
    surface (e.g. a staff dispatch UI). See Section 15 of the project
    directive.

    Deliberately generic (resource_type/resource_id as strings, not a
    ForeignKey to any one model) so this app has no dependency on any
    domain app -- domain apps depend on this one, never the reverse.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Who made the change. Null means the system made it "
        "(e.g. an automated process), not a person -- never left blank "
        "to mean 'unknown'.",
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=100)

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    source = models.CharField(
        max_length=50,
        help_text="Where this change originated, e.g. 'admin', 'api'.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def __str__(self) -> str:
        actor_label = self.actor.get_username() if self.actor else "system"
        return f"{actor_label} {self.action} {self.resource_type}:{self.resource_id}"

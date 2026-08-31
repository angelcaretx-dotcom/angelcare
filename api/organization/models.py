import uuid

from django.db import models


class Organization(models.Model):
    """
    AngelCare Transit itself, as a real record other domains can
    eventually anchor to (e.g. future Billing needs a "who is billing"
    entity), instead of being hard-coded config scattered across the
    codebase.

    Deliberately a single row for now -- no branches/locations/
    departments (Section 1 mentions them, but there's no confirmed
    second location, so modeling them would be speculative). Not
    enforced as a hard DB constraint (a future multi-entity ownership
    structure isn't impossible), just as an admin-level practical
    limit -- see OrganizationAdmin.

    This does NOT replace web/src/lib/site-config.ts. The public
    website intentionally doesn't call the API for static content
    (ADR 0001's frontend/backend separation) -- this record is for
    internal/backend use as the system grows, not a sync target.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    legal_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    service_area = models.CharField(max_length=200)
    ein = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="EIN",
        help_text="Internal use only -- not published on the public site.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Organization"
        verbose_name_plural = "Organization"

    def __str__(self) -> str:
        return self.legal_name

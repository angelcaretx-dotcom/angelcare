import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class DocumentType(models.TextChoices):
    """
    Kept minimal and grounded in what's actually needed today (backing
    up the expiration dates Driver/Vehicle already track) rather than
    guessing every category Section 16 could eventually need. Adding a
    new type later is a code change, not a schema change -- see
    docs/decisions/0009-document-domain.md.
    """

    DRIVER_LICENSE = "driver_license", "Driver's License"
    VEHICLE_REGISTRATION = "vehicle_registration", "Vehicle Registration"
    VEHICLE_INSURANCE = "vehicle_insurance", "Vehicle Insurance"
    VEHICLE_INSPECTION = "vehicle_inspection", "Vehicle Inspection Certificate"
    OTHER = "other", "Other"


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class Document(models.Model):
    """
    A file attached to any domain record (currently: Driver, Vehicle;
    the generic relation means Passenger, Trip, or a future domain can
    attach documents too without this app depending on them).

    Deliberately NOT versioned yet (Section 16 mentions "versioning") --
    re-uploading a replacement creates a new Document row rather than
    overwriting one, so historical truth is preserved (the project's
    own data-retention principle), but there's no explicit
    supersedes/superseded-by chain yet. That's real complexity worth
    adding once there's an actual re-upload workflow to design it
    around, not speculatively now.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    document_type = models.CharField(max_length=30, choices=DocumentType.choices)
    file = models.FileField(upload_to="documents/%Y/%m/")
    expiration_date = models.DateField(
        null=True, blank=True, help_text="Leave blank if this document type doesn't expire."
    )

    status = models.CharField(
        max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.PENDING
    )
    rejection_reason = models.TextField(blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_document_type_display()} ({self.status})"

    def clean(self):
        if self.status == DocumentStatus.REJECTED and not self.rejection_reason.strip():
            raise ValidationError(
                {"rejection_reason": "Required when rejecting a document."}
            )

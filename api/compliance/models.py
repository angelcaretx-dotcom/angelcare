import uuid

from django.db import models


class ComplianceRecordType(models.TextChoices):
    """
    Generic, industry-standard categories for the kinds of regulatory
    paperwork any transportation business holds -- not specific to any
    real AngelCare Transit license/policy/registration. Which of these
    AngelCare actually holds, their numbers, and their issuing
    authorities are explicitly UNKNOWN (see
    docs/business-decisions-log.md) and not represented here.
    """

    LICENSE = "license", "License"
    PERMIT = "permit", "Permit"
    INSURANCE_POLICY = "insurance_policy", "Insurance Policy"
    CERTIFICATION = "certification", "Certification"
    REGISTRATION = "registration", "Registration"
    OTHER = "other", "Other"


class ComplianceRecordStatus(models.TextChoices):
    """Archival status, not a delete flag -- same convention as every other domain."""

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    INACTIVE = "inactive", "Inactive"


class ComplianceRecord(models.Model):
    """
    A regulatory item AngelCare Transit holds at the business level --
    a license, permit, insurance policy, certification, or
    registration -- distinct from the per-Driver license and
    per-Vehicle registration/inspection already tracked in their own
    domains (ADR 0007).

    Deliberately limited to universal, confirmable structure (a
    descriptive name, generic type, issuing authority, reference
    number, issued/expiration dates, status, notes) -- NOT any real
    AngelCare license/policy/EIN/registration data, since none of
    that is confirmed (REQUIRES OFFICIAL SOURCE / REQUIRES BUSINESS
    DECISION -- see docs/decisions/0020-compliance-domain.md).
    Inventing values for these fields would mean fabricating
    regulatory claims, which this project's directive explicitly
    forbids -- every real record here must be entered by staff from
    an actual document.

    Supporting evidence (the actual license/policy PDF) attaches via
    the existing generic `documents.Document` framework (ADR 0009),
    the same way Driver/Vehicle attach their credential documents --
    no new file-handling code needed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(
        max_length=200,
        help_text="Descriptive name, e.g. 'General Liability Insurance'.",
    )
    record_type = models.CharField(max_length=20, choices=ComplianceRecordType.choices)

    issuing_authority = models.CharField(max_length=200, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)

    issued_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ComplianceRecordStatus.choices,
        default=ComplianceRecordStatus.ACTIVE,
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"

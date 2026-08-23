import uuid

from django.core.validators import RegexValidator
from django.db import models

phone_validator = RegexValidator(
    regex=r"^[0-9()+\-.\s]{7,20}$",
    message="Enter a valid phone number.",
)


class EmploymentType(models.TextChoices):
    EMPLOYEE = "employee", "Employee"
    CONTRACTOR = "contractor", "Contractor"


class DriverStatus(models.TextChoices):
    """Archival/eligibility status, not a delete flag."""

    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    SUSPENDED = "suspended", "Suspended"
    TERMINATED = "terminated", "Terminated"


class Driver(models.Model):
    """
    A driver providing transportation for AngelCare Transit.

    Deliberately limited to universal, confirmable facts (name, contact,
    employment type, driver's license + its expiration, status) --
    NOT insurance requirements, background checks, training records,
    certifications, medical/fitness documentation, or drug testing.
    Those require real, state-specific NEMT regulatory requirements or
    company policy that hasn't been confirmed (REQUIRES OFFICIAL SOURCE
    / REQUIRES BUSINESS DECISION -- see
    docs/decisions/0007-driver-vehicle-domains.md) and a Document domain
    that doesn't exist yet. Inventing structured fields for those would
    mean fabricating compliance-relevant data, which this project's
    directive explicitly forbids.

    license_expiration_date is tracked now so a future dispatch/
    assignment feature can block assigning a driver with an expired
    license (Section 7) -- but that enforcement doesn't exist yet,
    since there's no assignment feature to enforce it in.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    legal_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, validators=[phone_validator])
    email = models.EmailField(blank=True)

    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices)

    license_number = models.CharField(max_length=50)
    license_expiration_date = models.DateField()

    status = models.CharField(
        max_length=20, choices=DriverStatus.choices, default=DriverStatus.ACTIVE
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["legal_name"]

    def __str__(self) -> str:
        return f"{self.legal_name} ({self.status})"

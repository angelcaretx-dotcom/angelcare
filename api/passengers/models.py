import uuid

from django.core.validators import RegexValidator
from django.db import models

from transportation.models import ServiceType

phone_validator = RegexValidator(
    regex=r"^[0-9()+\-.\s]{7,20}$",
    message="Enter a valid phone number.",
)


class PassengerStatus(models.TextChoices):
    """
    Archival status, not a delete flag -- per the project's data
    retention principle (never destroy historical business records;
    prefer status changes to deletion).
    """

    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class Passenger(models.Model):
    """
    A real person AngelCare Transit provides transportation for, as a
    standalone record independent of any one trip request -- so repeat
    customers, mobility profiles, and history are possible.

    Deliberately does NOT yet include: authorized representatives,
    facility relationships, payer information, or documents. None of
    those have a home yet (no Facility/Payer/Document domain exists),
    and there's no confirmed need for them -- see
    docs/decisions/0006-passenger-domain.md.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    legal_name = models.CharField(max_length=200)
    preferred_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="If different from legal name (e.g. a nickname).",
    )

    phone = models.CharField(max_length=20, validators=[phone_validator])
    email = models.EmailField(blank=True)

    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_phone = models.CharField(
        max_length=20, blank=True, validators=[phone_validator]
    )

    preferred_service_type = models.CharField(
        max_length=20, choices=ServiceType.choices, blank=True
    )
    mobility_notes = models.TextField(blank=True)

    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=PassengerStatus.choices, default=PassengerStatus.ACTIVE
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["legal_name"]

    def __str__(self) -> str:
        return self.preferred_name or self.legal_name

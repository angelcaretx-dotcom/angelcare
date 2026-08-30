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

    Deliberately does NOT yet include: authorized representatives or
    facility relationships. Neither has a home yet (no Facility domain
    exists), and there's no confirmed need for them -- see
    docs/decisions/0006-passenger-domain.md. `payer` was added in
    ADR 0015 once a Payer domain existed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Deliberately nullable and staff-set only -- a passenger's funding
    # source isn't always known at intake, and this project never auto-
    # infers business-critical relationships. See
    # docs/decisions/0015-payer-broker-domain.md.
    payer = models.ForeignKey(
        "payers.Payer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="passengers",
    )

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

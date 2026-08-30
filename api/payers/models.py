import uuid

from django.core.validators import RegexValidator
from django.db import models

phone_validator = RegexValidator(
    regex=r"^[0-9()+\-.\s]{7,20}$",
    message="Enter a valid phone number.",
)


class PayerType(models.TextChoices):
    """
    Generic, industry-standard NEMT funding-source categories -- not
    specific to any real AngelCare Transit relationship. Which actual
    payers/brokers AngelCare works with is explicitly UNKNOWN (see
    docs/business-decisions-log.md) and not represented here.
    """

    MEDICAID_MCO = "medicaid_mco", "Medicaid MCO"
    BROKER = "broker", "Broker"
    PRIVATE_PAY = "private_pay", "Private Pay"
    FACILITY_CONTRACT = "facility_contract", "Facility Contract"
    OTHER = "other", "Other"


class PayerStatus(models.TextChoices):
    """Archival status, not a delete flag -- same convention as Passenger/Driver/Vehicle."""

    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class Payer(models.Model):
    """
    A funding source AngelCare Transit can bill or coordinate rides
    through -- a Medicaid MCO, a broker, a facility with a direct
    contract, or private pay.

    Deliberately limited to universal, confirmable structure (name,
    generic type, contact info, notes, status) -- NOT rate schedules,
    contract terms, EDI/billing integration, authorization/eligibility
    verification, or claims. None of that is grounded in a real,
    confirmed AngelCare relationship (REQUIRES BUSINESS DECISION -- see
    docs/decisions/0015-payer-broker-domain.md) and inventing it would
    mean fabricating business-critical data, which this project's
    directive explicitly forbids.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=200)
    payer_type = models.CharField(max_length=20, choices=PayerType.choices)

    contact_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)

    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=PayerStatus.choices, default=PayerStatus.ACTIVE
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

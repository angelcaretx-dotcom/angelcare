import uuid
from datetime import date

from django.conf import settings
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

    # The driver app login (ADR 0029): a plain, non-staff account. Drivers
    # sign in with this driver's email or phone, never a username.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="driver_profile",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["legal_name"]

    def __str__(self) -> str:
        return f"{self.legal_name} ({self.status})"


class CredentialType(models.TextChoices):
    """
    What a driver must hold besides the license on the Driver record
    (ADR 0036). Owner decision 2026-11-05: all of these are required for
    every driver; wheelchair securement only for wheelchair/stretcher trips.
    """

    MVR = "mvr", "Motor vehicle record (MVR) check"
    BACKGROUND_CHECK = "background_check", "Criminal background check"
    DRUG_SCREEN = "drug_screen", "Drug and alcohol screen"
    CPR_FIRST_AID = "cpr_first_aid", "CPR / first aid"
    DEFENSIVE_DRIVING = "defensive_driving", "Defensive driving"
    WHEELCHAIR_SECUREMENT = "wheelchair_securement", "Wheelchair securement (e.g. PASS)"


REQUIRED_FOR_EVERY_TRIP = [
    CredentialType.MVR,
    CredentialType.BACKGROUND_CHECK,
    CredentialType.DRUG_SCREEN,
    CredentialType.CPR_FIRST_AID,
    CredentialType.DEFENSIVE_DRIVING,
]
# Service types where the rider travels in their wheelchair or on a stretcher.
SECUREMENT_SERVICES = {"wheelchair", "stretcher"}


class DriverCredential(models.Model):
    """
    One credential a driver holds (ADR 0036). Renewing adds a new row:
    the newest of each type is the current one, older rows are history.
    The scanned document itself goes in the driver's Documents.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="credentials")
    credential_type = models.CharField(max_length=30, choices=CredentialType.choices)
    completed_on = models.DateField("date done / issued")
    expires_on = models.DateField(null=True, blank=True, help_text="Leave blank if it never expires.")
    reference = models.CharField(max_length=100, blank=True, help_text="Certificate or report number.")
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["credential_type", "-completed_on"]
        verbose_name = "credential"

    def __str__(self) -> str:
        return self.get_credential_type_display()

    def is_valid_on(self, day: date) -> bool:
        return self.expires_on is None or self.expires_on >= day


def current_credentials(driver) -> dict:
    """The newest credential of each type the driver holds."""
    current = {}
    for credential in driver.credentials.order_by("-completed_on", "-created_at"):
        current.setdefault(credential.credential_type, credential)
    return current


def missing_credentials(driver, *, service_type: str = "", payer=None, day: date | None = None) -> list[str]:
    """
    Labels of the credentials this driver lacks, or has expired, for a
    trip of this service and payer (ADR 0036). Empty means good to go.
    """
    day = day or date.today()
    needed = list(REQUIRED_FOR_EVERY_TRIP)
    if service_type in SECUREMENT_SERVICES:
        needed.append(CredentialType.WHEELCHAIR_SECUREMENT)
    if payer is not None:
        needed += [t for t in payer.required_credentials if t not in needed]
    current = current_credentials(driver)
    labels = dict(CredentialType.choices)
    return [
        labels.get(kind, kind)
        for kind in needed
        if kind not in current or not current[kind].is_valid_on(day)
    ]

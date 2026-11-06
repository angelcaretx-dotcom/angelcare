import uuid

from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class ClaimStatus(models.TextChoices):
    """Claim lifecycle, not a delete flag -- same never-delete principle as every other domain."""

    DRAFT = "draft", "Draft (not in a file yet)"
    SUBMITTED = "submitted", "Submitted"
    REJECTED = "rejected", "Rejected (277CA: fix and resend)"
    ACCEPTED = "accepted", "Accepted"
    DENIED = "denied", "Denied"
    PAID = "paid", "Paid"


class Claim(models.Model):
    """
    A record that an Invoice was submitted to a Payer for
    reimbursement.

    Deliberately limited to universal, confirmable structure (which
    invoice, which payer, an amount claimed, a status, the payer's own
    reference number once known, and when it was submitted/responded
    to) -- NOT EDI (837/835) file generation or parsing, clearinghouse
    integration, automatic status polling, appeals, or remittance
    advice parsing. None of that is grounded in a real, confirmed
    AngelCare claims process (REQUIRES BUSINESS DECISION -- see
    docs/decisions/0019-claims-domain.md); every field here is entered
    by staff by hand, same as Billing (ADR 0018).

    Never auto-created when an Invoice is marked sent -- staff create
    a Claim explicitly via /admin/, same manual-linking philosophy as
    TripRequest.passenger (ADR 0006), Passenger.payer (ADR 0017), and
    Invoice (ADR 0018).

    `payer` is required (unlike the optional payer on Invoice/
    Passenger) -- a claim only makes sense once there's someone to
    submit it to; a private-pay invoice has no claim at all.

    Multiple Claims can exist for one Invoice (FK, not OneToOne) --
    real claims get resubmitted after a denial, and that history
    shouldn't be destroyed to make room for a new one.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.PROTECT, related_name="claims"
    )
    payer = models.ForeignKey(
        "payers.Payer", on_delete=models.PROTECT, related_name="claims"
    )

    claim_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="The payer's own reference number, once known.",
    )
    amount_claimed = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    amount_paid = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    status = models.CharField(
        max_length=20, choices=ClaimStatus.choices, default=ClaimStatus.SUBMITTED
    )

    submitted_date = models.DateField(null=True, blank=True)
    response_date = models.DateField(null=True, blank=True)

    notes = models.TextField(blank=True)

    # Electronic claims to TMHP (ADR 0040).
    patient_control_number = models.CharField(
        max_length=20, blank=True, help_text="Our claim ID in the 837P (CLM01); responses come back by it."
    )
    service_date = models.DateField(null=True, blank=True)
    diagnosis_code = models.CharField(max_length=10, blank=True, help_text="ICD-10, without the dot.")
    modifier_1 = models.CharField(max_length=2, blank=True, help_text="e.g. origin/destination modifier.")
    modifier_2 = models.CharField(max_length=2, blank=True)
    replaces = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacements",
        help_text="The claim this one corrects or resubmits (sent as a replacement).",
    )
    claim_file = models.ForeignKey(
        "claims.ClaimFile", null=True, blank=True, on_delete=models.SET_NULL, related_name="claims"
    )
    response_details = models.JSONField(default=list, blank=True, help_text="Rejection/denial reasons from TMHP.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Claim {self.patient_control_number or self.id} -- ${self.amount_claimed} ({self.status})"

    @property
    def filing_deadline(self):
        """TMHP's 95-day filing deadline from the date of service (owner, 2026-11-05)."""
        return self.service_date + timedelta(days=FILING_DAYS) if self.service_date else None


FILING_DAYS = 95


class ClaimSettings(models.Model):
    """
    Everything TMHP assigns or the 837P needs about us (ADR 0040), entered
    by an Administrator from TMHP's enrollment and EDI paperwork. Nothing
    here is guessed: files can't be made until it's complete.
    """

    tmhp_payer = models.ForeignKey(
        "payers.Payer", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The payer record whose invoices are claimed through TMHP.",
    )
    submitter_id = models.CharField("TMHP EDI submitter ID", max_length=15, blank=True)
    submitter_contact_name = models.CharField(max_length=60, blank=True)
    submitter_contact_phone = models.CharField(max_length=20, blank=True, help_text="10 digits.")
    receiver_name = models.CharField(max_length=60, blank=True, help_text="As TMHP's companion guide gives it.")
    receiver_id = models.CharField("receiver ID", max_length=15, blank=True, help_text="From TMHP's companion guide.")
    interchange_qualifier = models.CharField(
        max_length=2, default="ZZ", help_text="ISA05/ISA07 ID qualifier, from TMHP's companion guide."
    )
    payer_id = models.CharField("payer ID (2010BB)", max_length=80, blank=True, help_text="From TMHP's companion guide.")
    billing_npi = models.CharField("billing NPI", max_length=10, blank=True)
    tpi = models.CharField("TPI (Texas Provider Identifier)", max_length=20, blank=True)
    taxonomy_code = models.CharField(max_length=10, blank=True)
    billing_street = models.CharField(max_length=55, blank=True, help_text="A street address, not a P.O. box.")
    billing_city = models.CharField(max_length=30, blank=True)
    billing_state = models.CharField(max_length=2, blank=True)
    billing_zip = models.CharField("billing ZIP+4", max_length=9, blank=True, help_text="9 digits, no dash.")
    place_of_service = models.CharField(max_length=2, blank=True, help_text="Two digits, from TMHP's guidance.")
    procedure_code = models.CharField(max_length=5, default="T2003")
    mileage_code = models.CharField(
        max_length=5, blank=True, help_text="Leave blank to bill miles inside the trip line."
    )
    default_diagnosis_code = models.CharField(max_length=10, blank=True)
    default_modifier_1 = models.CharField(max_length=2, blank=True)
    default_modifier_2 = models.CharField(max_length=2, blank=True)
    production = models.BooleanField(
        default=False, help_text="Untick while testing with TMHP (files are marked Test); tick once TMHP approves."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "claims setting"
        verbose_name_plural = "claims settings"

    def __str__(self) -> str:
        return "TMHP claims settings"

    REQUIRED = [
        ("tmhp_payer", "TMHP payer"), ("submitter_id", "submitter ID"), ("receiver_name", "receiver name"),
        ("receiver_id", "receiver ID"), ("payer_id", "payer ID"), ("billing_npi", "billing NPI"), ("tpi", "TPI"),
        ("taxonomy_code", "taxonomy code"), ("billing_street", "billing street"), ("billing_city", "billing city"),
        ("billing_state", "billing state"), ("billing_zip", "billing ZIP+4"), ("place_of_service", "place of service"),
        ("submitter_contact_name", "contact name"), ("submitter_contact_phone", "contact phone"),
    ]

    def missing(self) -> list[str]:
        return [label for field, label in self.REQUIRED if not getattr(self, field)]

    @classmethod
    def load(cls) -> "ClaimSettings":
        return cls.objects.first() or cls.objects.create()


class ClaimFile(models.Model):
    """One 837P file: made here, uploaded to TMHP by staff (until automated submission is set up)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    control_number = models.PositiveIntegerField(unique=True, help_text="ISA13 / GS06.")
    content = models.TextField()
    production = models.BooleanField(default=False)
    acknowledged = models.CharField(max_length=20, blank=True, help_text="From the 999: accepted / rejected.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "837P file"

    def __str__(self) -> str:
        return f"837P #{self.control_number:09d} ({self.created_at:%b %d, %Y})"

    @property
    def filename(self) -> str:
        return f"angelcare-837p-{self.control_number:09d}{'' if self.production else '-TEST'}.txt"


class ResponseFile(models.Model):
    """A file back from TMHP (999, 277CA or 835), kept as received, with what it changed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10)
    original_name = models.CharField(max_length=255)
    content = models.TextField()
    summary = models.JSONField(default=list, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "TMHP response file"

    def __str__(self) -> str:
        return f"{self.kind} {self.original_name}"

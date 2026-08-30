import uuid

from django.core.validators import MinValueValidator
from django.db import models


class ClaimStatus(models.TextChoices):
    """Claim lifecycle, not a delete flag -- same never-delete principle as every other domain."""

    SUBMITTED = "submitted", "Submitted"
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Claim {self.id} -- ${self.amount_claimed} ({self.status})"

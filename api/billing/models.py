import uuid

from django.core.validators import MinValueValidator
from django.db import models


class InvoiceStatus(models.TextChoices):
    """Billing lifecycle, not a delete flag -- same never-delete principle as every other domain."""

    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    PAID = "paid", "Paid"
    VOID = "void", "Void"


class Invoice(models.Model):
    """
    A bill for one completed Trip.

    Deliberately limited to universal, confirmable structure (which
    trip, who's being billed, an amount, a status, and when it was
    issued/paid) -- NOT rate schedules, automatic amount calculation,
    EDI or clearinghouse/billing-vendor integration, payment
    processing, or invoice PDF generation. None of that is grounded in
    a real, confirmed AngelCare billing process (REQUIRES BUSINESS
    DECISION -- see docs/decisions/0018-billing-domain.md); `amount`
    is entered by staff by hand, same as every other field here.

    Never auto-created when a Trip completes -- staff create an
    Invoice explicitly via /admin/, same manual-linking philosophy as
    TripRequest.passenger (ADR 0006) and Passenger.payer (ADR 0017):
    inventing an auto-billing workflow that doesn't exist yet would be
    worse than requiring a human in the loop.

    `payer` is captured on the invoice itself (not read live off
    `trip.trip_request.passenger.payer`) so a later change to a
    passenger's payer doesn't silently rewrite billing history. Null
    means private pay / self-pay, same convention as Passenger.payer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    trip = models.ForeignKey(
        "trips.Trip", on_delete=models.PROTECT, related_name="invoices"
    )
    payer = models.ForeignKey(
        "payers.Payer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )

    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT
    )

    issued_date = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invoice {self.id} -- ${self.amount} ({self.status})"

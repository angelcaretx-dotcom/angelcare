import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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
    # Calculated invoices (ADR 0039): the rate card used and how the
    # amount was worked out, kept so a later rate change never rewrites it.
    rate_card = models.ForeignKey(
        "billing.RateCard", null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    lines = models.JSONField(default=list, blank=True, help_text="How the amount was calculated.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invoice {self.id} -- ${self.amount} ({self.status})"



def money(**kwargs):
    return models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], **kwargs)


class RateCard(models.Model):
    """
    What a payer is charged (ADR 0039), entered by an Administrator from
    the payer's contract. A card applies from its start date; a price
    change is a new card with a later start, so invoices already made keep
    the card they used. One card with no payer is the private-pay rate.
    A blank base rate means that service isn't billed on this card.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="e.g. 'MTM 2027 contract'.")
    payer = models.ForeignKey(
        "payers.Payer", null=True, blank=True, on_delete=models.PROTECT, related_name="rate_cards",
        help_text="Leave blank for the private-pay rate (riders with no payer).",
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text="Leave blank to keep using it.")

    base_ambulatory = money(null=True, blank=True)
    base_wheelchair = money(null=True, blank=True)
    base_stretcher = money(null=True, blank=True)
    base_equipment_delivery = money(null=True, blank=True)
    per_mile = money(default=0, help_text="Per billable mile, after any included miles.")
    included_miles = models.DecimalField(
        max_digits=7, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="Miles covered by the base rate.",
    )
    wait_free_minutes = models.PositiveIntegerField(default=15)
    wait_per_minute = money(default=0, help_text="Charged after the free minutes, on wait-and-return rides.")
    no_show_fee = money(default=0)
    after_hours_fee = money(default=0)
    after_hours_start = models.TimeField(null=True, blank=True, help_text="e.g. 18:00 (Texas time).")
    after_hours_end = models.TimeField(null=True, blank=True, help_text="e.g. 06:00 (Texas time).")
    weekends_after_hours = models.BooleanField(default=False, help_text="All of Saturday and Sunday count as after-hours.")
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["payer__name", "-effective_from"]
        verbose_name = "rate card"

    def __str__(self) -> str:
        return self.name

    def clean(self):
        errors = {}
        if self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "The end date is before the start date."
        if bool(self.after_hours_start) != bool(self.after_hours_end):
            errors["after_hours_end"] = "Set both the after-hours start and end, or neither."
        same_start = RateCard.objects.filter(payer=self.payer, effective_from=self.effective_from).exclude(pk=self.pk)
        if same_start.exists():
            errors["effective_from"] = f"“{same_start.first().name}” already starts on that date for this payer."
        if errors:
            raise ValidationError(errors)

    def base_for(self, service_type: str):
        return getattr(self, f"base_{service_type}", None)


class DriverPayPlan(models.Model):
    """
    How a driver is paid (ADR 0039), entered by an Administrator. Any mix
    of the four: a flat amount per trip (optionally another for wheelchair
    trips), per billable mile, per hour on duty, and a share of the trip
    charge. A change is a new plan with a later start date.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey("drivers.Driver", on_delete=models.PROTECT, related_name="pay_plans")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    per_trip = money(default=0, help_text="For each completed or no-show trip.")
    per_trip_wheelchair = money(null=True, blank=True, help_text="Instead of the per-trip amount on wheelchair trips.")
    per_mile = money(default=0)
    hourly = money(default=0, help_text="Per hour from the start-of-day vehicle check to the end-of-day odometer.")
    percent_of_charge = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Share of what the trip is billed, e.g. 40 for 40%.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["driver__legal_name", "-effective_from"]
        verbose_name = "driver pay plan"

    def __str__(self) -> str:
        return f"{self.driver.legal_name} from {self.effective_from:%b %d, %Y}"

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "The end date is before the start date."})

import math
import uuid
from decimal import Decimal

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

    Each service level can have its own rates (ADR 0048): a loading fee, a
    per-mile rate and a wait rate, with wait time charged in blocks, and
    the booking and loading fees charged once for a round trip. Left
    blank, those fall back to the card-wide rates, so a card entered
    before them prices exactly as it did.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="e.g. 'MTM 2027 contract'.")
    payer = models.ForeignKey(
        "payers.Payer", null=True, blank=True, on_delete=models.PROTECT, related_name="rate_cards",
        help_text="Leave blank for the private-pay rate (riders with no payer).",
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text="Leave blank to keep using it.")

    base_ambulatory = money(null=True, blank=True, verbose_name="booking fee (base rate)")
    base_wheelchair = money(null=True, blank=True, verbose_name="booking fee (base rate)")
    base_equipment_delivery = money(null=True, blank=True, verbose_name="booking fee (base rate)")
    loading_ambulatory = money(null=True, blank=True, verbose_name="loading fee")
    loading_wheelchair = money(null=True, blank=True, verbose_name="loading fee")
    loading_equipment_delivery = money(null=True, blank=True, verbose_name="loading fee")
    fees_on_return = models.BooleanField(
        "charge booking and loading on the ride back", default=True,
        help_text="Untick to charge the booking fee and the loading fee once for a round trip, not on each ride.",
    )
    per_mile = money(default=0, help_text="Per billable mile, after any included miles.")
    per_mile_ambulatory = money(null=True, blank=True, verbose_name="per mile",
                                help_text="Blank uses the card's per-mile rate.")
    per_mile_wheelchair = money(null=True, blank=True, verbose_name="per mile",
                                help_text="Blank uses the card's per-mile rate.")
    per_mile_equipment_delivery = money(null=True, blank=True, verbose_name="per mile",
                                        help_text="Blank uses the card's per-mile rate.")
    included_miles = models.DecimalField(
        max_digits=7, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="Miles covered by the base rate.",
    )
    wait_free_minutes = models.PositiveIntegerField(default=15)
    wait_block_minutes = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)],
        help_text="Wait is charged in blocks of this many minutes, each started block in full. 1 = by the minute.",
    )
    wait_per_minute = money(
        default=0, verbose_name="wait rate",
        help_text="Per block (per minute when the block is 1), after the free minutes, on wait-and-return rides.",
    )
    wait_rate_ambulatory = money(null=True, blank=True, verbose_name="wait rate",
                                 help_text="Per wait block. Blank uses the card's wait rate.")
    wait_rate_wheelchair = money(null=True, blank=True, verbose_name="wait rate",
                                 help_text="Per wait block. Blank uses the card's wait rate.")
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
        # A blank or unreadable start date is the form's error to report, not a crash here.
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
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

    def loading_for(self, service_type: str) -> Decimal:
        return getattr(self, f"loading_{service_type}", None) or Decimal(0)

    def per_mile_for(self, service_type: str) -> Decimal:
        own = getattr(self, f"per_mile_{service_type}", None)
        return self.per_mile if own is None else own

    def wait_rate_for(self, service_type: str) -> Decimal:
        own = getattr(self, f"wait_rate_{service_type}", None)
        return self.wait_per_minute if own is None else own

    def wait_blocks(self, minutes: int) -> int:
        """Blocks charged for a wait: nothing for the free minutes, then each started block in full."""
        return math.ceil(max(0, minutes - self.wait_free_minutes) / max(1, self.wait_block_minutes))

    def wait_label(self, minutes: int) -> str:
        blocks = "" if self.wait_block_minutes <= 1 else f", {self.wait_block_minutes}-minute blocks"
        return f"Wait time ({minutes} min, first {self.wait_free_minutes} free{blocks})"


class Quote(models.Model):
    """
    A price emailed to the person who asked for a ride (ADR 0047), worked
    out from the rate card before the ride instead of told over the phone.
    One row per email sent: a changed price is a new quote, so what the
    customer was told is never rewritten. `lines` keeps how the amount was
    worked out, so a later rate change (or a deleted card) doesn't alter it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trip_request = models.ForeignKey(
        "transportation.TripRequest", on_delete=models.CASCADE, related_name="quotes"
    )
    rate_card = models.ForeignKey(
        "billing.RateCard", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes"
    )
    miles = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)],
        help_text="One-way miles the price was worked out from.",
    )
    wait_minutes = models.PositiveIntegerField(
        default=0, help_text="Expected wait at the destination, on wait-and-return rides."
    )
    lines = models.JSONField(default=list, blank=True, help_text="How the amount was calculated.")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    sent_to = models.EmailField()
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    delivered = models.BooleanField(
        default=True, help_text="Off when the email couldn't be sent (see the email log)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "quote"

    def __str__(self) -> str:
        return f"${self.amount} quote ({self.created_at:%b %d, %Y})"


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
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "The end date is before the start date."})

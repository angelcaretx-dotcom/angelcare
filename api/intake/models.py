"""
Trip intake (ADR 0038): staff upload the order file a broker, HHSC, a
facility or a partner sends, in that sender's own layout (CSV, Excel or
a text PDF). Each line becomes an ImportRow; clean rows become Trips,
anything doubtful waits in review for a person. Nothing is guessed.
"""

import uuid

from django.conf import settings
from django.db import models

from trips.models import TripSource


class ImportBatch(models.Model):
    """One uploaded file. The original is kept, privately, for as long as its trips are."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=20, choices=TripSource.choices)
    payer = models.ForeignKey("payers.Payer", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    file = models.FileField(upload_to="intake/%Y/%m/")
    original_name = models.CharField(max_length=255)
    headers = models.JSONField(default=list)
    template = models.ForeignKey(
        "intake.MappingTemplate", on_delete=models.SET_NULL, null=True, blank=True, related_name="batches"
    )
    field_map = models.JSONField(default=dict, help_text="Our field → the sender's column.")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "trip import"

    def __str__(self) -> str:
        return f"{self.original_name} ({self.created_at:%b %d, %Y})"

    @property
    def reference(self) -> str:
        return f"#{str(self.id)[:8].upper()}"

    def counts(self) -> dict:
        counts = {status: 0 for status in RowStatus.values}
        for status in self.rows.values_list("status", flat=True):
            counts[status] += 1
        counts["total"] = sum(counts.values())
        return counts


class MappingTemplate(models.Model):
    """
    How one sender lays out their file: which of their columns is which
    of our fields. Saved once, it maps that sender's next file by itself.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="e.g. 'Baylor discharge manifest'.")
    source = models.CharField(max_length=20, choices=TripSource.choices)
    payer = models.ForeignKey("payers.Payer", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    headers = models.JSONField(default=list, help_text="The sender's column names, as last seen.")
    field_map = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "column mapping"

    def __str__(self) -> str:
        return self.name


class RowStatus(models.TextChoices):
    READY = "ready", "Ready"
    REVIEW = "review", "Needs review"
    DUPLICATE = "duplicate", "Duplicate (skipped)"
    CREATED = "created", "Trip created"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"


class ImportRow(models.Model):
    """One line of an uploaded file, from the sender's text to a Trip."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    number = models.PositiveIntegerField(help_text="Line in the file.")
    raw = models.JSONField(default=dict, help_text="The sender's columns, as read.")
    values = models.JSONField(default=dict, help_text="Our fields, as mapped (and as edited in review).")
    data = models.JSONField(default=dict, help_text="The trip details read from the values.")
    problems = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=RowStatus.choices, default=RowStatus.REVIEW)
    passenger = models.ForeignKey("passengers.Passenger", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    trip = models.OneToOneField(
        "trips.Trip", on_delete=models.SET_NULL, null=True, blank=True, related_name="import_row"
    )
    return_trip = models.OneToOneField(
        "trips.Trip", on_delete=models.SET_NULL, null=True, blank=True, related_name="import_return_row"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["batch", "number"]
        verbose_name = "imported row"

    def __str__(self) -> str:
        return f"Row {self.number}"

    @property
    def hard_problems(self) -> list[str]:
        return [p["message"] for p in self.problems if p.get("hard")]

    @property
    def soft_problems(self) -> list[str]:
        return [p["message"] for p in self.problems if not p.get("hard")]

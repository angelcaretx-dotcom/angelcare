import uuid

from django.db import models


class VehicleStatus(models.TextChoices):
    """Archival/availability status, not a delete flag."""

    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "In maintenance"
    OUT_OF_SERVICE = "out_of_service", "Out of service"
    INACTIVE = "inactive", "Inactive"


class Vehicle(models.Model):
    """
    A vehicle in AngelCare Transit's fleet.

    Deliberately limited to universal, confirmable facts (identity,
    physical capability, registration/inspection expiration, status) --
    NOT insurance details, maintenance/mileage/fuel history, repairs,
    or incidents. Those need a Document/Maintenance domain that doesn't
    exist yet and real operational data this project can't invent --
    see docs/decisions/0007-driver-vehicle-domains.md.

    wheelchair_capable/stretcher_capable are independent booleans (not
    a single "vehicle type" choice) because a single vehicle can serve
    more than one service type -- e.g. a wheelchair-accessible van can
    also carry ambulatory passengers.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    make = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    year = models.PositiveIntegerField()
    license_plate = models.CharField(max_length=20)

    wheelchair_capable = models.BooleanField(default=False)
    stretcher_capable = models.BooleanField(default=False)
    passenger_capacity = models.PositiveIntegerField()

    registration_expiration_date = models.DateField()
    inspection_expiration_date = models.DateField()

    status = models.CharField(
        max_length=20, choices=VehicleStatus.choices, default=VehicleStatus.ACTIVE
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["make", "model"]

    def __str__(self) -> str:
        return f"{self.year} {self.make} {self.model} ({self.license_plate})"

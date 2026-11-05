import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


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
    # ADR 0036: missing or expired insurance blocks assigning the vehicle.
    insurance_expiration_date = models.DateField(
        null=True, blank=True, help_text="When this vehicle's insurance cover ends."
    )

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


# The daily pre-trip check (ADR 0034). Keys are stored, so labels can be
# reworded later without changing past records.
CHECK_ITEMS = [
    ("brakes", "Brakes, including parking brake"),
    ("lights", "Headlights, brake lights and turn signals"),
    ("tires", "Tires (pressure, tread, no damage)"),
    ("horn", "Horn"),
    ("wipers", "Windshield and wipers"),
    ("mirrors", "Mirrors"),
    ("seat_belts", "Seat belts"),
    ("dash_warnings", "No dashboard warning lights"),
    ("leaks", "No fluid leaks under the vehicle"),
    ("doors", "Doors, steps and handrails"),
    ("first_aid", "First-aid kit"),
    ("extinguisher", "Fire extinguisher"),
    ("interior", "Interior clean and safe"),
]
WHEELCHAIR_ITEMS = [
    ("lift_ramp", "Wheelchair lift or ramp works"),
    ("securement", "Wheelchair tie-downs and occupant belts"),
]
STRETCHER_ITEMS = [("stretcher", "Stretcher and its locks")]
ALL_CHECK_ITEMS = dict(CHECK_ITEMS + WHEELCHAIR_ITEMS + STRETCHER_ITEMS)


def check_items_for(vehicle) -> list[tuple[str, str]]:
    items = list(CHECK_ITEMS)
    if vehicle.wheelchair_capable:
        items += WHEELCHAIR_ITEMS
    if vehicle.stretcher_capable:
        items += STRETCHER_ITEMS
    return items


class VehicleCheck(models.Model):
    """
    A vehicle's day (ADR 0034): the driver's pre-trip inspection and
    odometer reading before the first trip, and the odometer again at
    the end of the day. Once per vehicle per Texas day.

    A failed item takes the vehicle out of service (no trips can start
    in it or be assigned to it) until an Administrator marks it fixed.
    Written by the driver app; staff can only read it, and clear it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="checks")
    service_date = models.DateField(help_text="The Texas calendar day.")
    driver = models.ForeignKey("drivers.Driver", on_delete=models.PROTECT, related_name="vehicle_checks")
    checked_at = models.DateTimeField(default=timezone.now, help_text="Server time.")
    start_odometer = models.PositiveIntegerField("odometer at start of day")
    failed_items = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, help_text="What's wrong, from the driver.")

    end_odometer = models.PositiveIntegerField("odometer at end of day", null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        "drivers.Driver", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cleared_note = models.TextField("what was fixed", blank=True)

    class Meta:
        ordering = ["-service_date", "vehicle"]
        verbose_name = "daily vehicle check"
        constraints = [
            models.UniqueConstraint(fields=["vehicle", "service_date"], name="one_check_per_vehicle_per_day"),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle} — {self.service_date:%b %d, %Y}"

    @property
    def passed(self) -> bool:
        return not self.failed_items

    @property
    def blocks_vehicle(self) -> bool:
        return bool(self.failed_items) and self.cleared_at is None

    @property
    def miles(self) -> int | None:
        if self.end_odometer is None:
            return None
        return self.end_odometer - self.start_odometer

    def failed_labels(self) -> list[str]:
        return [ALL_CHECK_ITEMS.get(key, key) for key in self.failed_items]

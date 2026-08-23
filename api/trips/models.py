import uuid
from datetime import date

from django.core.exceptions import ValidationError
from django.db import models


class TripStatus(models.TextChoices):
    """
    Deliberately coarser than the project directive's full example
    state machine (REQUESTED -> VERIFIED -> ... -> ARRIVED_DESTINATION
    -> COMPLETED). That machine assumes real-time updates from a
    driver-facing app, which doesn't exist yet -- states nothing can
    ever transition into aren't useful, they're decoration. This is
    the subset a human dispatcher can actually operate today by hand
    in /admin/. See docs/decisions/0008-trip-lifecycle-and-dispatch.md.
    """

    SCHEDULED = "scheduled", "Scheduled"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No show"


class Trip(models.Model):
    """
    An actual scheduled trip: a reviewed TripRequest with a driver and
    vehicle assigned. Distinct from TripRequest, which is just intake --
    a TripRequest may never become a Trip (declined, cancelled, etc.),
    and a Trip only exists once staff have committed real resources
    to it.

    One TripRequest becomes at most one Trip (OneToOne) -- recurring
    trips (Section 4's "recurring transportation" service type) aren't
    built yet; each occurrence would need its own TripRequest/Trip
    pair for now.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    trip_request = models.OneToOneField(
        "transportation.TripRequest",
        on_delete=models.PROTECT,
        related_name="trip",
    )
    driver = models.ForeignKey(
        "drivers.Driver", on_delete=models.PROTECT, related_name="trips"
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, related_name="trips"
    )

    scheduled_pickup_datetime = models.DateTimeField(
        help_text="Confirmed pickup time, stored in UTC -- may differ "
        "from the trip request's originally requested time."
    )
    status = models.CharField(
        max_length=20, choices=TripStatus.choices, default=TripStatus.SCHEDULED
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_pickup_datetime"]

    def __str__(self) -> str:
        return f"Trip for {self.trip_request.full_name} ({self.status})"

    def clean(self):
        errors = {}

        if self.trip_request_id and self.trip_request.passenger_id is None:
            errors["trip_request"] = (
                "This trip request isn't linked to a Passenger yet -- "
                "link one in the Trip Requests admin before scheduling a trip."
            )

        today = date.today()

        if self.driver_id:
            if self.driver.status != "active":
                errors["driver"] = (
                    f"Driver status is '{self.driver.get_status_display()}', "
                    "not Active -- cannot assign to a trip."
                )
            elif self.driver.license_expiration_date < today:
                errors["driver"] = (
                    f"Driver's license expired {self.driver.license_expiration_date} "
                    "-- cannot assign to a trip."
                )

        if self.vehicle_id:
            if self.vehicle.status != "active":
                errors["vehicle"] = (
                    f"Vehicle status is '{self.vehicle.get_status_display()}', "
                    "not Active -- cannot assign to a trip."
                )
            elif self.vehicle.registration_expiration_date < today:
                errors["vehicle"] = (
                    f"Vehicle registration expired "
                    f"{self.vehicle.registration_expiration_date} -- cannot assign "
                    "to a trip."
                )
            elif self.vehicle.inspection_expiration_date < today:
                errors["vehicle"] = (
                    f"Vehicle inspection expired {self.vehicle.inspection_expiration_date} "
                    "-- cannot assign to a trip."
                )

        if errors:
            raise ValidationError(errors)

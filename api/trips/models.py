import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from transportation.models import ServiceType


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


class TripSource(models.TextChoices):
    """
    How the trip reached us -- distinct from who pays (`payer`). An MTM
    trip is source MTM and (usually) payer MTM, but a facility booking
    can be paid privately, and subcontract work is paid by the partner.
    """

    WEBSITE = "website", "Website request"
    PHONE = "phone", "Phone"
    FACILITY = "facility", "Facility booking"
    MTM = "mtm", "MTM"
    SAFERIDE = "saferide", "SafeRide"
    HHSC = "hhsc", "HHSC (prior-authorized)"
    SUBCONTRACT = "subcontract", "Subcontract"
    PRIVATE_PAY = "private_pay", "Private pay"


class TripLeg(models.TextChoices):
    OUTBOUND = "outbound", "Outbound (to appointment)"
    RETURN = "return", "Return (home)"
    ONE_WAY = "one_way", "One way"


class Trip(models.Model):
    """
    The central operational record (ADR 0028): one rider, one ride from
    A to B, with a driver and vehicle once assigned. Billing, claims,
    proof of service and the SLA numbers all hang off this.

    A trip carries its own rider, addresses and times, so it doesn't
    need a website request: broker, phone, facility and (later)
    recurring trips are created directly. When it does come from a
    website request, blank fields are filled from that request.

    Driver and vehicle are optional: an unassigned trip waits on the
    day board. Starting or completing a trip requires both.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    trip_request = models.OneToOneField(
        "transportation.TripRequest",
        on_delete=models.PROTECT,
        related_name="trip",
        null=True,
        blank=True,
        help_text="The website request this trip came from, if any.",
    )
    # Nullable in the database only so trips that predate ADR 0028 could
    # be backfilled safely; clean() requires it.
    passenger = models.ForeignKey(
        "passengers.Passenger",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
    )

    source = models.CharField(
        max_length=20, choices=TripSource.choices, default=TripSource.PHONE
    )
    subcontract_partner = models.CharField(
        max_length=100,
        blank=True,
        help_text="For subcontract trips: AMTS, Gray Stone, American Medical Solutions, ...",
    )
    payer = models.ForeignKey(
        "payers.Payer",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
        help_text="Who pays for this trip. Defaults to the passenger's payer.",
    )
    broker_trip_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="The broker's own trip ID (MTM, SafeRide, subcontract partner).",
    )
    prior_authorization_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="Prior authorization number (HHSC / Medicaid), if required.",
    )

    leg = models.CharField(max_length=10, choices=TripLeg.choices, default=TripLeg.ONE_WAY)
    outbound_trip = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        related_name="return_trip",
        null=True,
        blank=True,
        help_text="For a return leg: the outbound trip it brings the rider home from.",
    )
    will_call = models.BooleanField(
        default=False,
        help_text="Return pickup time isn't fixed: the rider calls when ready. "
        "The pickup time is an estimate.",
    )

    service_type = models.CharField(max_length=20, choices=ServiceType.choices, blank=True)
    pickup_address = models.TextField(blank=True)
    dropoff_address = models.TextField(blank=True)

    driver = models.ForeignKey(
        "drivers.Driver",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
    )

    scheduled_pickup_datetime = models.DateTimeField(
        help_text="Confirmed pickup time, stored in UTC -- may differ "
        "from the trip request's originally requested time."
    )
    appointment_datetime = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the rider must be at the destination (outbound legs).",
    )
    status = models.CharField(
        max_length=20, choices=TripStatus.choices, default=TripStatus.SCHEDULED
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_pickup_datetime"]
        constraints = [
            # Re-entering (or, from Phase 28, re-importing) the same broker
            # trip must not create a second trip.
            models.UniqueConstraint(
                fields=["source", "broker_trip_number"],
                condition=~models.Q(broker_trip_number=""),
                name="unique_broker_trip_number_per_source",
                violation_error_message="A trip with this broker trip number "
                "already exists for this source.",
            ),
        ]
        indexes = [models.Index(fields=["scheduled_pickup_datetime"])]

    def __str__(self) -> str:
        who = self.passenger.legal_name if self.passenger_id else "unknown passenger"
        return f"Trip for {who} ({self.get_status_display()})"

    @property
    def is_assigned(self) -> bool:
        return bool(self.driver_id and self.vehicle_id)

    def fill_from_request(self) -> None:
        """Copy anything left blank from the website request it came from."""
        if not self.trip_request_id:
            return
        request = self.trip_request
        if not self.passenger_id and request.passenger_id:
            self.passenger_id = request.passenger_id
        if not self.pickup_address:
            self.pickup_address = request.pickup_address
        if not self.dropoff_address:
            self.dropoff_address = request.dropoff_address
        if not self.service_type:
            self.service_type = request.service_type

    def fill_payer_from_passenger(self) -> None:
        if not self.payer_id and self.passenger_id and self.passenger.payer_id:
            self.payer_id = self.passenger.payer_id

    def save(self, *args, **kwargs):
        self.fill_from_request()
        self.fill_payer_from_passenger()
        super().save(*args, **kwargs)

    def clean(self):
        self.fill_from_request()
        self.fill_payer_from_passenger()
        errors = {}

        if not self.passenger_id:
            if self.trip_request_id:
                errors["trip_request"] = (
                    "This trip request isn't linked to a Passenger yet -- "
                    "link one in the Trip Requests admin, or choose the passenger here."
                )
            else:
                errors["passenger"] = "Choose the passenger for this trip."

        for field in ("pickup_address", "dropoff_address", "service_type"):
            if not getattr(self, field):
                errors[field] = "This field is required."

        if self.source == TripSource.SUBCONTRACT and not self.subcontract_partner:
            errors["subcontract_partner"] = "Which partner is this subcontract trip for?"

        if self.outbound_trip_id:
            if self.outbound_trip_id == self.id:
                errors["outbound_trip"] = "A trip can't be its own outbound leg."
            elif self.leg != TripLeg.RETURN:
                errors["leg"] = "A trip linked to an outbound trip must be a Return leg."
            elif (
                self.scheduled_pickup_datetime
                and self.scheduled_pickup_datetime < self.outbound_trip.scheduled_pickup_datetime
            ):
                errors["scheduled_pickup_datetime"] = (
                    "A return pickup can't be before its outbound pickup."
                )
        if self.will_call and self.leg != TripLeg.RETURN:
            errors["will_call"] = "Only a return leg can be will-call."

        if self.status in (TripStatus.IN_PROGRESS, TripStatus.COMPLETED) and not self.is_assigned:
            errors["status"] = (
                "Assign a driver and a vehicle before starting or completing a trip."
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


class TurnDownReason(models.TextChoices):
    NO_DRIVER = "no_driver", "No driver available"
    NO_VEHICLE = "no_vehicle", "No suitable vehicle (e.g. wheelchair / stretcher)"
    OUTSIDE_AREA = "outside_area", "Outside service area"
    SHORT_NOTICE = "short_notice", "Too short notice"
    RATE = "rate", "Rate too low"
    OTHER = "other", "Other"


class TurnDown(models.Model):
    """
    A trip offered to us that we declined (ADR 0028). Brokers grade
    acceptance rate (SafeRide/MTM expect ~95%), so declines are counted
    with a reason -- the SLA dashboard (Phase 31) reads these.

    Deliberately holds no rider details: the broker's trip number is
    enough to reconcile, and a declined trip shouldn't leave PHI behind.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    source = models.CharField(max_length=20, choices=TripSource.choices)
    payer = models.ForeignKey(
        "payers.Payer",
        on_delete=models.PROTECT,
        related_name="turn_downs",
        null=True,
        blank=True,
    )
    broker_trip_number = models.CharField(max_length=50, blank=True)
    offered_at = models.DateTimeField(
        default=timezone.now, help_text="When the trip was offered to us."
    )
    requested_pickup_datetime = models.DateTimeField(null=True, blank=True)
    service_type = models.CharField(max_length=20, choices=ServiceType.choices, blank=True)
    reason = models.CharField(max_length=20, choices=TurnDownReason.choices)
    notes = models.TextField(
        blank=True, help_text="No rider names or health details here, please."
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-offered_at"]
        verbose_name = "turn-down"

    def __str__(self) -> str:
        ref = f" {self.broker_trip_number}" if self.broker_trip_number else ""
        return f"{self.get_source_display()}{ref}: {self.get_reason_display()}"

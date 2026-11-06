import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from transportation.models import ReturnRide, ServiceType


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
    facility = models.ForeignKey(
        "facilities.Facility",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
        help_text="The facility this ride is for. Its portal users can see the trip (ADR 0042).",
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
    driver_waits = models.BooleanField(
        default=False,
        help_text="Wait-and-return: the outbound driver waits at the destination "
        "and brings the rider back. The pickup time is an estimate.",
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
    # Made by a standing order (ADR 0035), for this Texas date.
    standing_order = models.ForeignKey(
        "trips.StandingOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trips", editable=False,
    )
    standing_date = models.DateField(null=True, blank=True, editable=False)
    # For per-mile charges and pay (ADR 0039): from the import file's miles
    # column, or typed by staff. The odometer is per vehicle-day only.
    billable_miles = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Trip miles for billing and driver pay (from the import, or entered here).",
    )

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
            # A standing order makes each leg once per date, however often
            # the generator runs.
            models.UniqueConstraint(
                fields=["standing_order", "standing_date", "leg"],
                condition=models.Q(standing_order__isnull=False),
                name="one_trip_per_standing_order_date_leg",
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
        if not self.facility_id and request.facility_id:
            self.facility_id = request.facility_id

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
        if self.driver_waits and self.leg != TripLeg.RETURN:
            errors["driver_waits"] = "Only a return leg can be wait-and-return."
        if self.driver_waits and self.will_call:
            errors["driver_waits"] = "Choose either will-call or wait-and-return, not both."

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

        self._check_new_assignment(errors, today)

        if errors:
            raise ValidationError(errors)

    def _check_new_assignment(self, errors, today):
        """
        Credentials and insurance (ADR 0036), checked when a driver or
        vehicle is put on the trip (or the service/payer changes), so
        trips assigned before these rules existed keep working.
        """
        from drivers.models import missing_credentials

        before = None
        if not self._state.adding:
            before = Trip.objects.filter(pk=self.pk).values("driver_id", "vehicle_id", "service_type", "payer_id").first()
        changed = before is None or (before["service_type"], before["payer_id"]) != (self.service_type, self.payer_id)

        if self.driver_id and "driver" not in errors and (changed or before["driver_id"] != self.driver_id):
            missing = missing_credentials(self.driver, service_type=self.service_type, payer=self.payer, day=today)
            if missing:
                errors["driver"] = (
                    f"{self.driver.legal_name} is missing or has expired: {', '.join(missing)} "
                    "-- add it on the driver's page before assigning."
                )

        if self.vehicle_id and "vehicle" not in errors and (before is None or before["vehicle_id"] != self.vehicle_id):
            ends = self.vehicle.insurance_expiration_date
            if ends is None:
                errors["vehicle"] = "No insurance date on file for this vehicle -- add it before assigning."
            elif ends < today:
                errors["vehicle"] = f"Vehicle insurance expired {ends} -- cannot assign to a trip."


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


class TripEventType(models.TextChoices):
    EN_ROUTE = "en_route", "En route to pickup"
    ARRIVED_PICKUP = "arrived_pickup", "Arrived at pickup"
    PICKED_UP = "picked_up", "Rider picked up"
    DROPPED_OFF = "dropped_off", "Rider dropped off"
    NO_SHOW = "no_show", "No-show"
    CANCELLED_AT_DOOR = "cancelled_at_door", "Cancelled at the door"


class TripEventReason(models.TextChoices):
    """Why a trip ended without a ride (no-show / cancelled at the door)."""

    NOT_PRESENT = "not_present", "Rider not at pickup"
    REFUSED = "refused", "Rider refused the ride"
    RIDER_CANCELLED = "rider_cancelled", "Rider cancelled at the door"
    UNSAFE = "unsafe", "Unsafe / unable to transport"
    ADDRESS_PROBLEM = "address_problem", "Wrong or unreachable address"
    OTHER = "other", "Other (see note)"


class SignatureMissingReason(models.TextChoices):
    """Why the rider didn't sign at pickup (ADR 0034)."""

    UNABLE = "unable", "Rider physically or cognitively unable to sign"
    DECLINED = "declined", "Rider declined to sign"
    OTHER = "other", "Other (see note)"


class TripEvent(models.Model):
    """
    One step in a trip's timeline (ADR 0029), recorded by the driver app
    with the server's clock and the phone's GPS position. Append-only:
    this is the proof of service a broker, HHSC or the VA audits, so a
    mistaken tap is corrected by staff with a note, never by editing or
    deleting the event.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=20, choices=TripEventType.choices)
    occurred_at = models.DateTimeField(
        default=timezone.now, help_text="Server time when the driver tapped."
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_m = models.FloatField(
        null=True, blank=True, help_text="GPS accuracy reported by the phone, in meters."
    )
    reason = models.CharField(max_length=20, choices=TripEventReason.choices, blank=True)
    note = models.TextField(blank=True)
    # At pickup (ADR 0034): the rider's signature on the driver's phone,
    # or why there isn't one. A small black-on-white PNG.
    signature = models.BinaryField(null=True, blank=True, editable=False)
    signed_by_name = models.CharField(
        max_length=200, blank=True, help_text="Who signed, if not the rider (e.g. a caregiver)."
    )
    signature_missing_reason = models.CharField(
        max_length=20, choices=SignatureMissingReason.choices, blank=True
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.CharField(max_length=20, default="driver_app")

    class Meta:
        ordering = ["occurred_at"]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} at {self.occurred_at:%Y-%m-%d %H:%M}"

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


WEEKDAYS = [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]


class StandingOrder(models.Model):
    """
    A rider's repeating ride, e.g. dialysis Mon/Wed/Fri at 6:00 AM
    (ADR 0035). Trips are created from it automatically, 14 days ahead;
    each is an ordinary trip that dispatch can adjust. Times are Texas
    time. No trips are created after the prior authorization's end date.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    passenger = models.ForeignKey("passengers.Passenger", on_delete=models.PROTECT, related_name="standing_orders")
    source = models.CharField(max_length=20, choices=TripSource.choices, default=TripSource.PHONE)
    subcontract_partner = models.CharField(max_length=200, blank=True)
    payer = models.ForeignKey(
        "payers.Payer", on_delete=models.PROTECT, related_name="standing_orders", null=True, blank=True,
        help_text="Leave blank to use the passenger's payer.",
    )
    facility = models.ForeignKey(
        "facilities.Facility", on_delete=models.PROTECT, related_name="standing_orders", null=True, blank=True,
        help_text="e.g. the dialysis center: its portal users see these trips (ADR 0042).",
    )
    prior_authorization_number = models.CharField(max_length=50, blank=True)
    prior_authorization_ends = models.DateField(
        null=True, blank=True,
        help_text="Last day the authorization covers. No trips are created after it; "
        "staff are emailed 14 days before.",
    )
    auth_warning_sent_for = models.DateField(null=True, blank=True, editable=False)

    service_type = models.CharField(max_length=20, choices=ServiceType.choices)
    pickup_address = models.TextField()
    dropoff_address = models.TextField()

    weekdays = models.JSONField(default=list, help_text="Days of the week the ride repeats on.")
    pickup_time = models.TimeField(help_text="Texas time.")
    appointment_time = models.TimeField(null=True, blank=True, help_text="Texas time, if there's an appointment.")
    return_ride = models.CharField(
        "ride back", max_length=20, choices=ReturnRide.choices, default=ReturnRide.NONE
    )
    return_time = models.TimeField(
        null=True, blank=True, help_text="Texas time; only for a ride back at a set time."
    )

    start_date = models.DateField(default=date.today)
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank to keep going.")
    active = models.BooleanField(default=True, help_text="Untick to pause without deleting.")

    driver = models.ForeignKey(
        "drivers.Driver", on_delete=models.PROTECT, related_name="standing_orders", null=True, blank=True,
        help_text="Regular driver: given each new trip when available.",
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, related_name="standing_orders", null=True, blank=True,
        help_text="Regular vehicle: given each new trip when in service.",
    )
    notes = models.TextField(blank=True, help_text="Copied to each trip's dispatch notes.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["passenger__legal_name", "pickup_time"]
        verbose_name = "standing order"

    def __str__(self) -> str:
        return f"{self.passenger} — {self.days_display()} {self.pickup_time:%I:%M %p}".replace(" 0", " ")

    def days_display(self) -> str:
        return "/".join(label for value, label in WEEKDAYS if value in self.weekdays) or "no days"

    @property
    def reference(self) -> str:
        return f"#{str(self.id)[:8].upper()}"

    def clean(self):
        errors = {}
        if not self.weekdays:
            errors["weekdays"] = "Choose at least one day."
        if self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "The end date is before the start date."
        if self.return_ride == ReturnRide.SCHEDULED:
            if not self.return_time:
                errors["return_time"] = "Set the time of the ride back."
            elif self.return_time <= self.pickup_time:
                errors["return_time"] = "The ride back must be after the pickup."
        elif self.return_time:
            errors["return_time"] = "Only a ride back at a set time has a time."
        if self.service_type == ServiceType.EQUIPMENT_DELIVERY and self.return_ride != ReturnRide.NONE:
            errors["return_ride"] = "Equipment delivery has no ride back."
        if self.source == TripSource.SUBCONTRACT and not self.subcontract_partner:
            errors["subcontract_partner"] = "Which partner is this subcontract work for?"
        if errors:
            raise ValidationError(errors)


class StandingOrderSkip(models.Model):
    """A date the ride doesn't happen (holiday, hospital stay); the series carries on."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(StandingOrder, on_delete=models.CASCADE, related_name="skips")
    date = models.DateField()
    reason = models.CharField(max_length=200, blank=True, help_text="No health details, please.")

    class Meta:
        ordering = ["date"]
        verbose_name = "skipped date"
        constraints = [models.UniqueConstraint(fields=["order", "date"], name="one_skip_per_order_per_date")]

    def __str__(self) -> str:
        return f"{self.date:%b %d, %Y}"

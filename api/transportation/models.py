import uuid

from django.core.validators import RegexValidator
from django.db import models

phone_validator = RegexValidator(
    regex=r"^[0-9()+\-.\s]{7,20}$",
    message="Enter a valid phone number.",
)


class ServiceType(models.TextChoices):
    """
    Services AngelCare Transit actually offers. Do not add a value here
    without confirming it in docs/business-decisions-log.md first.
    """

    AMBULATORY = "ambulatory", "Ambulatory"
    WHEELCHAIR = "wheelchair", "Wheelchair"
    STRETCHER = "stretcher", "Stretcher"


class TripRequestStatus(models.TextChoices):
    """
    Intake status for a trip request submitted via the public website.

    This is deliberately NOT the full trip lifecycle state machine
    (REQUESTED -> ... -> COMPLETED) described in the project's dispatch
    architecture — that belongs to a later phase once a request has been
    reviewed and turned into an actual scheduled trip. This status only
    tracks what has happened to the *request itself*.
    """

    NEW = "new", "New"
    REVIEWED = "reviewed", "Reviewed"
    CONTACTED = "contacted", "Contacted"
    SCHEDULED = "scheduled", "Scheduled"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"


class TripRequest(models.Model):
    """
    A transportation request submitted through the public website. This is
    an intake record, not a confirmed booking — staff review it and follow
    up with the requester.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, validators=[phone_validator])
    email = models.EmailField()

    pickup_address = models.TextField()
    dropoff_address = models.TextField()
    requested_datetime = models.DateTimeField(
        help_text="Requested pickup date/time, stored in UTC."
    )

    service_type = models.CharField(max_length=20, choices=ServiceType.choices)
    mobility_notes = models.TextField(blank=True)
    additional_notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=TripRequestStatus.choices,
        default=TripRequestStatus.NEW,
    )
    source = models.CharField(
        max_length=50,
        default="website",
        help_text="Where this request originated (website, phone, etc.).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.full_name} — {self.get_service_type_display()} ({self.status})"

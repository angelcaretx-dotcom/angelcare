import uuid

from django.db import models


class NotificationType(models.TextChoices):
    NEW_TRIP_REQUEST_STAFF = "new_trip_request_staff", "New trip request (staff)"
    NEW_TRIP_REQUEST_CUSTOMER = (
        "new_trip_request_customer",
        "New trip request confirmation (customer)",
    )


class NotificationStatus(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class NotificationLog(models.Model):
    """
    A record of every notification attempt, so a silent send failure is
    visible in /admin/ rather than invisible. This is deliberately
    separate from the business record it relates to (e.g. TripRequest)
    -- notifications are a side effect of a business event, not part of
    the event itself, and a failed notification must never block or
    corrupt the underlying business transaction.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    notification_type = models.CharField(max_length=50, choices=NotificationType.choices)
    recipient = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=NotificationStatus.choices)
    error_message = models.TextField(blank=True)

    # Generic reference to whatever triggered this notification (e.g. a
    # TripRequest id), without a hard foreign-key dependency on any one
    # domain app -- keeps this app usable by any future domain.
    related_object_type = models.CharField(max_length=100, blank=True)
    related_object_id = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.recipient} ({self.status})"

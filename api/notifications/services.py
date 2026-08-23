import logging

from django.conf import settings
from django.template.loader import render_to_string

from .models import NotificationLog, NotificationStatus, NotificationType
from .providers.base import EmailProvider
from .providers.email import DjangoEmailProvider

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Entry point for triggering notifications. Domain apps (e.g.
    transportation) call this; they never touch an EmailProvider or
    vendor SDK directly. A failure here is logged to NotificationLog
    and to the application log -- it must NEVER raise back into the
    caller, because a notification is a side effect of a business
    event, not the event itself. The underlying business record (e.g.
    the TripRequest) is already safely saved by the time this runs.
    """

    def __init__(self, email_provider: EmailProvider | None = None):
        self.email_provider = email_provider or DjangoEmailProvider()

    def notify_new_trip_request(self, trip_request) -> None:
        self._send_to_staff(trip_request)
        self._send_to_customer(trip_request)

    def _send_to_staff(self, trip_request) -> None:
        subject = f"New trip request: {trip_request.full_name} ({trip_request.get_service_type_display()})"
        body = render_to_string(
            "notifications/staff_new_trip_request.txt",
            {"trip_request": trip_request},
        )
        self._send(
            notification_type=NotificationType.NEW_TRIP_REQUEST_STAFF,
            to=settings.STAFF_NOTIFICATION_EMAIL,
            subject=subject,
            body_text=body,
            trip_request=trip_request,
        )

    def _send_to_customer(self, trip_request) -> None:
        subject = "We received your transportation request — AngelCare Transit"
        body = render_to_string(
            "notifications/customer_trip_request_confirmation.txt",
            {"trip_request": trip_request},
        )
        self._send(
            notification_type=NotificationType.NEW_TRIP_REQUEST_CUSTOMER,
            to=trip_request.email,
            subject=subject,
            body_text=body,
            trip_request=trip_request,
        )

    def _send(
        self,
        *,
        notification_type: str,
        to: str,
        subject: str,
        body_text: str,
        trip_request,
    ) -> None:
        try:
            self.email_provider.send(to=to, subject=subject, body_text=body_text)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # provider failure must be caught here, logged, and never
            # propagate into the request/response cycle.
            logger.error(
                "Notification send failed: type=%s recipient=%s error=%s",
                notification_type,
                to,
                exc,
            )
            NotificationLog.objects.create(
                notification_type=notification_type,
                recipient=to,
                status=NotificationStatus.FAILED,
                error_message=str(exc),
                related_object_type="TripRequest",
                related_object_id=str(trip_request.id),
            )
            return

        NotificationLog.objects.create(
            notification_type=notification_type,
            recipient=to,
            status=NotificationStatus.SENT,
            related_object_type="TripRequest",
            related_object_id=str(trip_request.id),
        )

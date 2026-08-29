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

    Every email is rendered as a branded HTML template (see
    notifications/templates/notifications/email_base.html and ADR
    0015) with a plain-text alternative always sent alongside it --
    the .txt template is not a legacy leftover, it's the accessible/
    deliverability-friendly fallback every professional transactional
    email should still carry.
    """

    def __init__(self, email_provider: EmailProvider | None = None):
        self.email_provider = email_provider or DjangoEmailProvider()

    def notify_new_trip_request(self, trip_request) -> None:
        self._send_to_staff(trip_request)
        self._send_to_customer(trip_request)

    def _send_to_staff(self, trip_request) -> None:
        subject = f"New trip request: {trip_request.full_name} ({trip_request.get_service_type_display()})"
        rows = self._staff_rows(trip_request)
        context = {
            "trip_request": trip_request,
            "rows": rows,
            "admin_url": f"{settings.SITE_URL}/admin/transportation/triprequest/{trip_request.id}/change/",
            "website_url": settings.WEBSITE_URL,
        }
        self._send(
            notification_type=NotificationType.NEW_TRIP_REQUEST_STAFF,
            to=settings.STAFF_NOTIFICATION_EMAIL,
            subject=subject,
            body_text=render_to_string("notifications/staff_new_trip_request.txt", context),
            body_html=render_to_string("notifications/staff_new_trip_request.html", context),
            trip_request=trip_request,
        )

    def _send_to_customer(self, trip_request) -> None:
        subject = "We received your transportation request — AngelCare Transit"
        rows = self._customer_rows(trip_request)
        context = {
            "trip_request": trip_request,
            "rows": rows,
            "website_url": settings.WEBSITE_URL,
        }
        self._send(
            notification_type=NotificationType.NEW_TRIP_REQUEST_CUSTOMER,
            to=trip_request.email,
            subject=subject,
            body_text=render_to_string("notifications/customer_trip_request_confirmation.txt", context),
            body_html=render_to_string("notifications/customer_trip_request_confirmation.html", context),
            trip_request=trip_request,
        )

    @staticmethod
    def _staff_rows(trip_request) -> list[tuple[str, str]]:
        rows = [
            ("Requester", trip_request.full_name),
            ("Phone", trip_request.phone),
            ("Email", trip_request.email),
            ("Service type", trip_request.get_service_type_display()),
            ("Requested pickup", trip_request.requested_datetime),
            ("Pickup address", trip_request.pickup_address),
            ("Drop-off address", trip_request.dropoff_address),
        ]
        if trip_request.mobility_notes:
            rows.append(("Mobility notes", trip_request.mobility_notes))
        if trip_request.additional_notes:
            rows.append(("Additional notes", trip_request.additional_notes))
        return rows

    @staticmethod
    def _customer_rows(trip_request) -> list[tuple[str, str]]:
        return [
            ("Service type", trip_request.get_service_type_display()),
            ("Requested pickup", trip_request.requested_datetime),
            ("Pickup address", trip_request.pickup_address),
            ("Drop-off address", trip_request.dropoff_address),
        ]

    def _send(
        self,
        *,
        notification_type: str,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None,
        trip_request,
    ) -> None:
        try:
            self.email_provider.send(to=to, subject=subject, body_text=body_text, body_html=body_html)
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

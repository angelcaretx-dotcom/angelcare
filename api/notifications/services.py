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
        # No rider details in the staff alert (ADR 0027): the email
        # passes through mail providers and inboxes we have no BAA
        # with. Staff open the request in the admin, which logs the view.
        subject = f"New trip request {self._reference(trip_request)} — log in to view"
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
            related=trip_request,
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
            related=trip_request,
        )

    @staticmethod
    def _reference(trip_request) -> str:
        return f"#{str(trip_request.id)[:8].upper()}"

    # --- Rider accounts (ADR 0031). No PHI: just a link, as ever. ---

    def send_rider_invite(self, account, link: str) -> None:
        self._send_link_email(
            account,
            NotificationType.RIDER_ACCOUNT_INVITE,
            subject="Track your rides with AngelCare Transit — set up your account",
            template="rider_account_invite",
            link=link,
        )

    def send_rider_password_reset(self, account, link: str) -> None:
        self._send_link_email(
            account,
            NotificationType.RIDER_PASSWORD_RESET,
            subject="Reset your AngelCare Transit password",
            template="rider_password_reset",
            link=link,
        )

    def _send_link_email(self, account, notification_type, *, subject, template, link) -> None:
        context = {"link": link, "website_url": settings.WEBSITE_URL}
        self._send(
            notification_type=notification_type,
            to=account.email,
            subject=subject,
            body_text=render_to_string(f"notifications/{template}.txt", context),
            body_html=render_to_string(f"notifications/{template}.html", context),
            related=account,
        )

    def notify_rider_cancelled(self, trip_request) -> None:
        reference = self._reference(trip_request)
        context = {
            "rows": [("Reference", reference), ("Requested pickup", trip_request.requested_datetime)],
            "admin_url": f"{settings.SITE_URL}/admin/transportation/triprequest/{trip_request.id}/change/",
            "website_url": settings.WEBSITE_URL,
        }
        self._send(
            notification_type=NotificationType.RIDER_CANCELLED_STAFF,
            to=settings.STAFF_NOTIFICATION_EMAIL,
            subject=f"Trip request {reference} cancelled by the rider",
            body_text=render_to_string("notifications/staff_rider_cancelled.txt", context),
            body_html=render_to_string("notifications/staff_rider_cancelled.html", context),
            related=trip_request,
        )

    @classmethod
    def _staff_rows(cls, trip_request) -> list[tuple[str, str]]:
        # Reference and timing only: enough to judge urgency, nothing
        # that identifies the rider or their health needs.
        return [
            ("Reference", cls._reference(trip_request)),
            ("Requested pickup", trip_request.requested_datetime),
            ("Submitted", trip_request.created_at),
        ]

    @classmethod
    def _customer_rows(cls, trip_request) -> list[tuple[str, str]]:
        # Reference only (ADR 0027): Resend has no confirmed BAA, so the
        # rider's name, addresses and pickup time stay out of email.
        return [("Reference", cls._reference(trip_request))]

    def _send(
        self,
        *,
        notification_type: str,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None,
        related,
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
                related_object_type=type(related).__name__,
                related_object_id=str(related.pk),
            )
            return

        NotificationLog.objects.create(
            notification_type=notification_type,
            recipient=to,
            status=NotificationStatus.SENT,
            related_object_type=type(related).__name__,
            related_object_id=str(related.pk),
        )

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from transportation.models import ServiceType, TripRequest

from .models import NotificationLog, NotificationStatus, NotificationType
from .providers.base import EmailProvider
from .services import NotificationService


class FailingEmailProvider(EmailProvider):
    def send(self, *, to: str, subject: str, body_text: str, body_html: str | None = None) -> None:
        raise RuntimeError("simulated provider outage")


class RecordingEmailProvider(EmailProvider):
    """Records calls instead of actually sending, for assertions."""

    def __init__(self):
        self.sent = []

    def send(self, *, to: str, subject: str, body_text: str, body_html: str | None = None) -> None:
        self.sent.append(
            {"to": to, "subject": subject, "body_text": body_text, "body_html": body_html}
        )


def make_trip_request(**overrides) -> TripRequest:
    defaults = {
        "full_name": "Alex Rivera",
        "phone": "817-555-0100",
        "email": "alex@example.com",
        "pickup_address": "1 Main St, Fort Worth, TX",
        "dropoff_address": "2 Clinic Rd, Fort Worth, TX",
        "requested_datetime": timezone.now() + timedelta(days=1),
        "service_type": ServiceType.WHEELCHAIR,
    }
    defaults.update(overrides)
    return TripRequest.objects.create(**defaults)


class NotificationServiceTests(TestCase):
    def test_sends_to_both_staff_and_customer(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        recipients = [msg["to"] for msg in provider.sent]
        self.assertIn("angelcaretransit@hotmail.com", recipients)
        self.assertIn("alex@example.com", recipients)
        self.assertEqual(len(provider.sent), 2)

    def test_successful_send_is_logged(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        logs = NotificationLog.objects.filter(related_object_id=str(trip_request.id))
        self.assertEqual(logs.count(), 2)
        self.assertTrue(all(log.status == NotificationStatus.SENT for log in logs))

    def test_provider_failure_does_not_raise(self):
        trip_request = make_trip_request()

        try:
            NotificationService(email_provider=FailingEmailProvider()).notify_new_trip_request(
                trip_request
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"notify_new_trip_request raised unexpectedly: {exc}")

    def test_provider_failure_is_logged_with_error(self):
        trip_request = make_trip_request()

        NotificationService(email_provider=FailingEmailProvider()).notify_new_trip_request(
            trip_request
        )

        logs = NotificationLog.objects.filter(related_object_id=str(trip_request.id))
        self.assertEqual(logs.count(), 2)
        self.assertTrue(all(log.status == NotificationStatus.FAILED for log in logs))
        self.assertTrue(all("simulated provider outage" in log.error_message for log in logs))

    def test_staff_email_links_to_the_request(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        staff_email = next(m for m in provider.sent if m["to"] == "angelcaretransit@hotmail.com")
        reference = f"#{str(trip_request.id)[:8].upper()}"
        self.assertIn(reference, staff_email["subject"])
        self.assertIn(reference, staff_email["body_text"])
        self.assertIn(
            f"/admin/transportation/triprequest/{trip_request.id}/change/",
            staff_email["body_text"],
        )

    def test_staff_email_carries_no_rider_details(self):
        """ADR 0027: no PHI in the staff alert -- subject, text or HTML."""
        provider = RecordingEmailProvider()
        trip_request = make_trip_request(
            full_name="Jamie Chen",
            phone="817-555-0199",
            email="jamie.chen@example.com",
            pickup_address="77 Oak Lane, Arlington, TX",
            dropoff_address="9 Dialysis Center Dr, Fort Worth, TX",
            mobility_notes="Uses a power wheelchair",
            additional_notes="Kidney dialysis three times a week",
        )

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        staff_email = next(m for m in provider.sent if m["to"] == "angelcaretransit@hotmail.com")
        for part in ("subject", "body_text", "body_html"):
            for detail in (
                "Jamie", "Chen", "555-0199", "jamie.chen@", "Oak Lane",
                "Dialysis Center", "power wheelchair", "Kidney",
                "Wheelchair",  # the service type hints at a health need
            ):
                self.assertNotIn(detail, staff_email[part], f"{detail!r} leaked into {part}")

    def test_customer_confirmation_carries_only_the_reference(self):
        """ADR 0027: no PHI in the rider's confirmation either."""
        provider = RecordingEmailProvider()
        trip_request = make_trip_request(
            full_name="Jamie Chen",
            phone="817-555-0199",
            email="jamie.chen@example.com",
            pickup_address="77 Oak Lane, Arlington, TX",
            dropoff_address="9 Dialysis Center Dr, Fort Worth, TX",
            mobility_notes="Uses a power wheelchair",
            additional_notes="Kidney dialysis three times a week",
        )

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        customer_email = next(m for m in provider.sent if m["to"] == "jamie.chen@example.com")
        reference = f"#{str(trip_request.id)[:8].upper()}"
        for part in ("subject", "body_text", "body_html"):
            for detail in (
                "Jamie", "Chen", "555-0199", "Oak Lane", "Dialysis Center",
                "power wheelchair", "Kidney", "Wheelchair",
            ):
                self.assertNotIn(detail, customer_email[part], f"{detail!r} leaked into {part}")
        self.assertIn(reference, customer_email["body_text"])
        self.assertIn(reference, customer_email["body_html"])

    def test_customer_email_contains_disclaimer(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        customer_email = next(m for m in provider.sent if m["to"] == "alex@example.com")
        self.assertIn("not a confirmed booking", customer_email["body_text"])
        self.assertIn("911", customer_email["body_text"])


class NotificationHtmlEmailTests(TestCase):
    """
    Covers the branded HTML formatting added by ADR 0015 -- every email
    is multipart/alternative (plain text + HTML), not text-only.
    """

    def test_every_send_includes_an_html_alternative(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        self.assertEqual(len(provider.sent), 2)
        for message in provider.sent:
            self.assertIsNotNone(message["body_html"])
            self.assertIn("<!DOCTYPE html>", message["body_html"])
            # Shared branded header present in every email type.
            self.assertIn("AngelCare Transit", message["body_html"])

    def test_staff_html_contains_admin_link(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        staff_email = next(m for m in provider.sent if m["to"] == "angelcaretransit@hotmail.com")
        self.assertIn(
            f"/admin/transportation/triprequest/{trip_request.id}/change/",
            staff_email["body_html"],
        )
        self.assertIn("Review in Admin", staff_email["body_html"])

    def test_customer_html_contains_emergency_disclaimer(self):
        provider = RecordingEmailProvider()
        trip_request = make_trip_request()

        NotificationService(email_provider=provider).notify_new_trip_request(trip_request)

        customer_email = next(m for m in provider.sent if m["to"] == "alex@example.com")
        self.assertIn("911", customer_email["body_html"])
        self.assertIn("Medical emergency", customer_email["body_html"])

    def test_django_email_provider_sends_real_multipart_message(self):
        """
        Exercises the actual DjangoEmailProvider (not the test double)
        against Django's locmem test backend, proving the HTML part is
        genuinely attached as multipart/alternative, not just present
        in a mock's recorded kwargs.
        """
        from django.core import mail

        trip_request = make_trip_request()

        NotificationService().notify_new_trip_request(trip_request)

        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertEqual(len(message.alternatives), 1)
            html_body, mimetype = message.alternatives[0]
            self.assertEqual(mimetype, "text/html")
            self.assertIn("AngelCare Transit", html_body)
            # The plain-text body (message.body) is still sent as the
            # primary part -- multipart/alternative, not HTML-only.
            self.assertTrue(message.body)

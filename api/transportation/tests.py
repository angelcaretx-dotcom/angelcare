from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from notifications.models import NotificationLog

from .models import TripRequest, TripRequestStatus


def valid_payload(**overrides):
    payload = {
        "full_name": "Jane Doe",
        "phone": "817-555-0100",
        "email": "jane@example.com",
        "pickup_address": "123 Main St, Fort Worth, TX",
        "dropoff_address": "456 Clinic Rd, Fort Worth, TX",
        "requested_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "service_type": "wheelchair",
        "mobility_notes": "Uses a manual wheelchair",
        "additional_notes": "",
    }
    payload.update(overrides)
    return payload


class TripRequestCreateTests(APITestCase):
    def setUp(self):
        self.url = reverse("transportation:trip-request-create")

    def test_valid_submission_creates_trip_request(self):
        response = self.client.post(self.url, valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TripRequest.objects.count(), 1)

        trip_request = TripRequest.objects.first()
        self.assertEqual(trip_request.full_name, "Jane Doe")
        self.assertEqual(trip_request.status, TripRequestStatus.NEW)
        self.assertEqual(trip_request.source, "website")

    def test_anonymous_users_can_submit(self):
        # Public endpoint: no auth required.
        response = self.client.post(self.url, valid_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_missing_required_field_is_rejected(self):
        payload = valid_payload()
        del payload["full_name"]

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("full_name", response.data)
        self.assertEqual(TripRequest.objects.count(), 0)

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            self.url, valid_payload(email="not-an-email"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_invalid_service_type_is_rejected(self):
        response = self.client.post(
            self.url, valid_payload(service_type="helicopter"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("service_type", response.data)

    def test_past_pickup_datetime_is_rejected(self):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        response = self.client.post(
            self.url, valid_payload(requested_datetime=past), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("requested_datetime", response.data)

    def test_blank_pickup_address_is_rejected(self):
        response = self.client.post(
            self.url, valid_payload(pickup_address="   "), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pickup_address", response.data)

    def test_response_does_not_leak_internal_fields(self):
        response = self.client.post(self.url, valid_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("status", response.data)
        self.assertNotIn("source", response.data)

    def test_list_and_retrieve_are_not_publicly_exposed(self):
        create_response = self.client.post(self.url, valid_payload(), format="json")
        trip_request_id = create_response.data["id"]

        list_response = self.client.get(self.url)
        self.assertIn(
            list_response.status_code,
            (status.HTTP_405_METHOD_NOT_ALLOWED,),
        )

        detail_response = self.client.get(f"{self.url}{trip_request_id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)


class TripRequestNotificationIntegrationTests(APITestCase):
    """
    Confirms the full path: a real API submission actually triggers
    real notification sends (via Django's test email backend) and logs
    them -- not just that NotificationService works in isolation.
    """

    def setUp(self):
        self.url = reverse("transportation:trip-request-create")
        mail.outbox = []

    def test_submitting_a_request_sends_staff_and_customer_emails(self):
        response = self.client.post(self.url, valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 2)

        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(recipients, {"angelcaretx@gmail.com", "jane@example.com"})

    def test_submitting_a_request_creates_notification_log_entries(self):
        response = self.client.post(self.url, valid_payload(), format="json")

        logs = NotificationLog.objects.filter(related_object_id=response.data["id"])
        self.assertEqual(logs.count(), 2)

    def test_notification_failure_does_not_break_trip_request_creation(self):
        with patch(
            "notifications.providers.email.DjangoEmailProvider.send",
            side_effect=RuntimeError("smtp down"),
        ):
            response = self.client.post(self.url, valid_payload(), format="json")

        # The trip request itself must still succeed -- a notification
        # outage is not a reason to lose a real customer's request.
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TripRequest.objects.count(), 1)


class TripRequestModelTests(APITestCase):
    def test_default_status_is_new(self):
        trip_request = TripRequest.objects.create(**{
            **valid_payload(),
            "requested_datetime": timezone.now() + timedelta(days=1),
        })
        self.assertEqual(trip_request.status, TripRequestStatus.NEW)

    def test_str_representation(self):
        trip_request = TripRequest.objects.create(**{
            **valid_payload(),
            "requested_datetime": timezone.now() + timedelta(days=1),
        })
        self.assertIn(trip_request.full_name, str(trip_request))

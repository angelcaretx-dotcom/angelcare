from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from notifications.models import NotificationLog
from passengers.models import Passenger

from verification.services import issue_token

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
    # The website confirms the email first (ADR 0033).
    payload.setdefault("verification_token", issue_token(payload["email"]))
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

    def test_public_submission_cannot_set_passenger_link(self):
        # Linking a request to a Passenger record is a deliberate staff
        # action in /admin/ (see passengers app), never settable by an
        # anonymous public submission.
        passenger = Passenger.objects.create(legal_name="Someone Else", phone="817-555-0111")

        response = self.client.post(
            self.url, valid_payload(passenger=str(passenger.id)), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        trip_request = TripRequest.objects.get(id=response.data["id"])
        self.assertIsNone(trip_request.passenger)

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
        self.assertIn("pickup_street", response.data)

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
        # Staff alert, rider confirmation, and (first booking from this
        # email) the rider account set-up link -- ADR 0031.
        self.assertEqual(len(mail.outbox), 3)

        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(recipients, {"angelcaretransit@hotmail.com", "jane@example.com"})

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


def model_fields(**overrides):
    """valid_payload as model fields: the API-only token dropped."""
    payload = valid_payload(**overrides)
    payload.pop("verification_token")
    return {**payload, "requested_datetime": timezone.now() + timedelta(days=1)}


class TripRequestModelTests(APITestCase):
    def test_default_status_is_new(self):
        trip_request = TripRequest.objects.create(**model_fields())
        self.assertEqual(trip_request.status, TripRequestStatus.NEW)

    def test_str_representation(self):
        trip_request = TripRequest.objects.create(**model_fields())
        self.assertIn(trip_request.full_name, str(trip_request))


def parts_payload(**overrides):
    """A booking as the website sends it since ADR 0032: address parts, no one-line address."""
    payload = valid_payload(**overrides)
    for side in ("pickup", "dropoff"):
        if f"{side}_street" not in overrides:
            payload.pop(f"{side}_address", None)
    defaults = {
        "pickup_street": "123 Main St",
        "pickup_unit": "Apt 4",
        "pickup_city": "Fort Worth",
        "pickup_state": "tx",
        "pickup_zip": "76104",
        "dropoff_street": "456 Clinic Rd",
        "dropoff_city": "Arlington",
        "dropoff_state": "TX",
        "dropoff_zip": "76010-1234",
    }
    return {**defaults, **payload}


class AddressPartsTests(APITestCase):
    def setUp(self):
        # Many bookings from one test client: keep the anonymous rate limit out of it.
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("transportation:trip-request-create")

    def test_parts_are_kept_and_composed_into_one_line(self):
        response = self.client.post(self.url, parts_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        request = TripRequest.objects.get()
        self.assertEqual(request.pickup_address, "123 Main St, Apt 4, Fort Worth, TX 76104")
        self.assertEqual(request.dropoff_address, "456 Clinic Rd, Arlington, TX 76010-1234")
        self.assertEqual((request.pickup_city, request.pickup_state, request.pickup_zip), ("Fort Worth", "TX", "76104"))

    def test_each_missing_or_bad_part_is_reported_on_its_field(self):
        response = self.client.post(
            self.url, parts_payload(pickup_city=" ", pickup_zip="7610", dropoff_state="ZZ"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"pickup_city", "pickup_zip", "dropoff_state"})

    def test_one_line_address_still_accepted_from_older_clients(self):
        response = self.client.post(self.url, valid_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        request = TripRequest.objects.get()
        self.assertEqual(request.pickup_address, "123 Main St, Fort Worth, TX")
        self.assertEqual(request.pickup_street, "")

    def test_editing_parts_recomposes_the_line(self):
        self.client.post(self.url, parts_payload(), format="json")
        request = TripRequest.objects.get()
        request.pickup_unit = ""
        request.save(update_fields=["pickup_unit"])
        request.refresh_from_db()
        self.assertEqual(request.pickup_address, "123 Main St, Fort Worth, TX 76104")


class ReturnRideTests(APITestCase):
    def setUp(self):
        # Many bookings from one test client: keep the anonymous rate limit out of it.
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("transportation:trip-request-create")
        self.pickup = timezone.now() + timedelta(days=1)

    def post(self, **overrides):
        return self.client.post(
            self.url, parts_payload(requested_datetime=self.pickup.isoformat(), **overrides), format="json"
        )

    def test_one_way_by_default(self):
        self.assertEqual(self.post().status_code, status.HTTP_201_CREATED)
        self.assertEqual(TripRequest.objects.get().return_ride, "none")

    def test_set_time_return_needs_a_time_after_the_pickup(self):
        response = self.post(return_ride="scheduled")
        self.assertIn("return_datetime", response.data)
        response = self.post(return_ride="scheduled", return_datetime=(self.pickup - timedelta(hours=1)).isoformat())
        self.assertIn("return_datetime", response.data)
        back = self.pickup + timedelta(hours=3)
        response = self.post(return_ride="scheduled", return_datetime=back.isoformat())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(TripRequest.objects.get().return_datetime, back)

    def test_will_call_and_wait_keep_no_time(self):
        for choice in ("will_call", "wait"):
            response = self.post(return_ride=choice, return_datetime=(self.pickup + timedelta(hours=2)).isoformat())
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertIsNone(TripRequest.objects.get(pk=response.data["id"]).return_datetime)

    def test_equipment_delivery_has_no_return_ride(self):
        response = self.post(service_type="equipment_delivery", return_ride="wait")
        self.assertIn("return_ride", response.data)

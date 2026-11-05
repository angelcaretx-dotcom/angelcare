from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog

from .models import Trip, TripLeg
from .tests import make_driver, make_passenger, make_trip_request, make_vehicle

PICKUP = timezone.now().replace(microsecond=0) + timedelta(days=3)


class ReturnLegFromAdminTests(TestCase):
    """Scheduling a website round trip creates the ride home too (ADR 0032)."""

    def setUp(self):
        self.user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        self.user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        self.passenger = make_passenger()
        self.driver = make_driver()
        self.vehicle = make_vehicle()

    def schedule(self, trip_request, **extra):
        # The admin reads typed times as Texas time.
        local = timezone.localtime(PICKUP, ZoneInfo("America/Chicago"))
        data = {
            "trip_request": str(trip_request.id),
            "driver": str(self.driver.id),
            "vehicle": str(self.vehicle.id),
            "scheduled_pickup_datetime_0": local.strftime("%Y-%m-%d"),
            "scheduled_pickup_datetime_1": local.strftime("%H:%M:%S"),
            "status": "scheduled",
            "source": "website",
            "leg": "one_way",
            "notes": "",
            "events-TOTAL_FORMS": "0",
            "events-INITIAL_FORMS": "0",
            **extra,
        }
        response = self.client.post("/admin/trips/trip/add/", data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        return Trip.objects.get(trip_request=trip_request)

    def request(self, **overrides):
        return make_trip_request(passenger=self.passenger, pickup_address="1 Home St", dropoff_address="2 Clinic Rd", **overrides)

    def test_set_time_return(self):
        back_at = PICKUP + timedelta(hours=3)
        outbound = self.schedule(self.request(return_ride="scheduled", return_datetime=back_at))
        back = outbound.return_trip
        self.assertEqual(outbound.leg, TripLeg.OUTBOUND)
        self.assertEqual((back.leg, back.pickup_address, back.dropoff_address), (TripLeg.RETURN, "2 Clinic Rd", "1 Home St"))
        self.assertEqual(back.scheduled_pickup_datetime, back_at)
        self.assertEqual((back.passenger, back.service_type), (self.passenger, outbound.service_type))
        self.assertFalse(back.will_call or back.driver_waits)
        self.assertIsNone(back.driver)  # dispatch assigns the ride home separately
        self.assertTrue(AuditLog.objects.filter(action="created", resource_id=str(back.pk)).exists())

    def test_will_call_return_is_an_estimate(self):
        outbound = self.schedule(self.request(return_ride="will_call"))
        back = outbound.return_trip
        self.assertTrue(back.will_call)
        self.assertEqual(back.scheduled_pickup_datetime, PICKUP + timedelta(hours=2))

    def test_wait_and_return_keeps_the_same_driver_and_vehicle(self):
        outbound = self.schedule(self.request(return_ride="wait"))
        back = outbound.return_trip
        self.assertTrue(back.driver_waits)
        self.assertEqual((back.driver, back.vehicle), (self.driver, self.vehicle))

    def test_one_way_request_gets_no_return_leg(self):
        outbound = self.schedule(self.request())
        self.assertEqual(outbound.leg, TripLeg.ONE_WAY)
        self.assertEqual(Trip.objects.count(), 1)


class DriverWaitsValidationTests(TestCase):
    def test_only_return_legs_can_wait(self):
        trip = Trip(
            passenger=make_passenger(),
            service_type="ambulatory",
            pickup_address="a",
            dropoff_address="b",
            scheduled_pickup_datetime=PICKUP,
            driver_waits=True,
        )
        with self.assertRaises(ValidationError) as caught:
            trip.full_clean()
        self.assertIn("driver_waits", caught.exception.message_dict)


class TexasTimeInTheAdminTests(TestCase):
    """The admin shows and reads Texas time; storage stays UTC (2026-11-05 day board bug)."""

    def setUp(self):
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")

    def test_ten_am_typed_is_ten_am_central_and_shows_as_ten_am_on_the_board(self):
        from datetime import datetime, timezone as dt_timezone

        request = make_trip_request(passenger=make_passenger())
        response = self.client.post(
            "/admin/trips/trip/add/",
            {
                "trip_request": str(request.id),
                "scheduled_pickup_datetime_0": "2027-01-15",
                "scheduled_pickup_datetime_1": "10:00:00",
                "status": "scheduled",
                "source": "website",
                "leg": "one_way",
                "notes": "",
                "events-TOTAL_FORMS": "0",
                "events-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        trip = Trip.objects.get()
        # January: Central is UTC-6.
        self.assertEqual(trip.scheduled_pickup_datetime, datetime(2027, 1, 15, 16, 0, tzinfo=dt_timezone.utc))
        board = self.client.get("/admin/trips/trip/board/?date=2027-01-15").content.decode()
        self.assertIn("10:00 AM", board)
        self.assertNotIn("4:00 PM", board)

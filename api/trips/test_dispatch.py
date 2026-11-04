from datetime import date, datetime, timedelta
from importlib import import_module
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from payers.models import Payer

from .board import build_board
from .models import Trip, TripLeg, TripSource, TripStatus, TurnDown
from .tests import make_driver, make_passenger, make_trip_request, make_vehicle

CENTRAL = ZoneInfo("America/Chicago")


def make_payer(name="MTM") -> Payer:
    return Payer.objects.create(name=name, payer_type="broker")


def direct_trip(passenger, **overrides) -> Trip:
    """A trip with no website request -- e.g. phoned in or from a broker."""
    defaults = {
        "passenger": passenger,
        "source": TripSource.PHONE,
        "service_type": "wheelchair",
        "pickup_address": "1 Main St",
        "dropoff_address": "2 Clinic Rd",
        "scheduled_pickup_datetime": timezone.now() + timedelta(days=1),
    }
    defaults.update(overrides)
    return Trip(**defaults)


class TripWithoutRequestTests(TestCase):
    def setUp(self):
        self.passenger = make_passenger()

    def test_a_trip_needs_no_website_request_or_driver(self):
        trip = direct_trip(self.passenger)
        trip.full_clean()
        trip.save()
        self.assertFalse(trip.is_assigned)

    def test_passenger_and_route_are_required(self):
        trip = Trip(scheduled_pickup_datetime=timezone.now())
        with self.assertRaises(ValidationError) as ctx:
            trip.full_clean()
        for field in ("passenger", "pickup_address", "dropoff_address", "service_type"):
            self.assertIn(field, ctx.exception.message_dict)

    def test_blank_fields_are_filled_from_the_website_request(self):
        request = make_trip_request(passenger=self.passenger, service_type="stretcher")
        trip = Trip(trip_request=request, scheduled_pickup_datetime=timezone.now())
        trip.full_clean()
        self.assertEqual(trip.passenger, self.passenger)
        self.assertEqual(trip.pickup_address, "1 Main St")
        self.assertEqual(trip.service_type, "stretcher")

    def test_payer_defaults_to_the_passengers_payer(self):
        self.passenger.payer = make_payer()
        self.passenger.save()
        trip = direct_trip(self.passenger)
        trip.save()
        self.assertEqual(trip.payer.name, "MTM")

    def test_cannot_start_or_complete_without_driver_and_vehicle(self):
        for status in (TripStatus.IN_PROGRESS, TripStatus.COMPLETED):
            with self.subTest(status=status), self.assertRaises(ValidationError) as ctx:
                direct_trip(self.passenger, status=status).full_clean()
            self.assertIn("status", ctx.exception.message_dict)

    def test_subcontract_trip_names_the_partner(self):
        with self.assertRaises(ValidationError) as ctx:
            direct_trip(self.passenger, source=TripSource.SUBCONTRACT).full_clean()
        self.assertIn("subcontract_partner", ctx.exception.message_dict)
        direct_trip(
            self.passenger, source=TripSource.SUBCONTRACT, subcontract_partner="AMTS"
        ).full_clean()


class BrokerNumberTests(TestCase):
    def setUp(self):
        self.passenger = make_passenger()

    def test_same_broker_number_twice_for_one_source_is_rejected(self):
        direct_trip(self.passenger, source=TripSource.MTM, broker_trip_number="M-100").save()
        with self.assertRaises(ValidationError) as ctx:
            direct_trip(self.passenger, source=TripSource.MTM, broker_trip_number="M-100").full_clean()
        self.assertIn("already exists", str(ctx.exception))

    def test_same_number_from_another_source_and_blank_numbers_are_fine(self):
        direct_trip(self.passenger, source=TripSource.MTM, broker_trip_number="100").save()
        direct_trip(self.passenger, source=TripSource.SAFERIDE, broker_trip_number="100").full_clean()
        direct_trip(self.passenger).save()
        direct_trip(self.passenger).full_clean()


class LegTests(TestCase):
    def setUp(self):
        self.passenger = make_passenger()
        self.outbound = direct_trip(self.passenger, leg=TripLeg.OUTBOUND)
        self.outbound.save()

    def test_return_leg_links_to_its_outbound_trip(self):
        back = direct_trip(
            self.passenger,
            leg=TripLeg.RETURN,
            outbound_trip=self.outbound,
            will_call=True,
            scheduled_pickup_datetime=self.outbound.scheduled_pickup_datetime + timedelta(hours=3),
        )
        back.full_clean()
        back.save()
        self.assertEqual(self.outbound.return_trip, back)

    def test_return_cannot_be_picked_up_before_the_outbound(self):
        back = direct_trip(
            self.passenger,
            leg=TripLeg.RETURN,
            outbound_trip=self.outbound,
            scheduled_pickup_datetime=self.outbound.scheduled_pickup_datetime - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError) as ctx:
            back.full_clean()
        self.assertIn("scheduled_pickup_datetime", ctx.exception.message_dict)

    def test_linked_trip_must_be_a_return_and_only_returns_are_will_call(self):
        with self.assertRaises(ValidationError) as ctx:
            direct_trip(self.passenger, outbound_trip=self.outbound).full_clean()
        self.assertIn("leg", ctx.exception.message_dict)
        with self.assertRaises(ValidationError) as ctx:
            direct_trip(self.passenger, will_call=True).full_clean()
        self.assertIn("will_call", ctx.exception.message_dict)


class BackfillMigrationTests(TestCase):
    def test_existing_trips_get_rider_and_route_from_their_request(self):
        payer = make_payer()
        passenger = make_passenger(payer=payer)
        request = make_trip_request(passenger=passenger, service_type="stretcher")
        trip = Trip.objects.create(trip_request=request, scheduled_pickup_datetime=timezone.now())
        # Simulate a pre-ADR 0028 row: only the request carried the details.
        Trip.objects.filter(pk=trip.pk).update(
            passenger=None, pickup_address="", dropoff_address="", service_type="",
            payer=None, source="phone",
        )

        migration = import_module("trips.migrations.0002_dispatch_foundations")
        migration.backfill_from_requests(apps, None)

        trip.refresh_from_db()
        self.assertEqual(trip.passenger, passenger)
        self.assertEqual(trip.pickup_address, "1 Main St")
        self.assertEqual(trip.service_type, "stretcher")
        self.assertEqual(trip.source, "website")
        self.assertEqual(trip.payer, payer)


class DayBoardTests(TestCase):
    DAY = date(2027, 3, 10)

    def setUp(self):
        self.user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        self.user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        self.passenger = make_passenger()
        self.driver = make_driver()
        self.vehicle = make_vehicle()

    def at(self, hour, minute=0, day=None):
        return datetime.combine(day or self.DAY, datetime.min.time(), tzinfo=CENTRAL).replace(
            hour=hour, minute=minute
        )

    def test_board_groups_unassigned_first_then_by_driver_in_central_time(self):
        early = direct_trip(self.passenger, scheduled_pickup_datetime=self.at(0, 30))
        late = direct_trip(self.passenger, scheduled_pickup_datetime=self.at(23, 30))
        assigned = direct_trip(
            self.passenger, driver=self.driver, vehicle=self.vehicle,
            scheduled_pickup_datetime=self.at(9),
        )
        next_day = direct_trip(self.passenger, scheduled_pickup_datetime=self.at(0, 30, self.DAY + timedelta(days=1)))
        cancelled = direct_trip(self.passenger, status=TripStatus.CANCELLED, scheduled_pickup_datetime=self.at(10))
        for t in (early, late, assigned, next_day, cancelled):
            t.save()

        board = build_board(self.DAY)

        self.assertEqual(board["unassigned"], [early, late])
        self.assertEqual(len(board["drivers"]), 1)
        self.assertEqual(board["drivers"][0].trips, [assigned])
        self.assertEqual(board["closed_count"], 1)

    def test_page_renders_and_the_view_is_audited(self):
        direct_trip(self.passenger, scheduled_pickup_datetime=self.at(8)).save()
        response = self.client.get(reverse("admin:trips_trip_board"), {"date": "2027-03-10"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs a driver (1)")
        self.assertContains(response, "8:00 AM")
        self.assertContains(response, 'name="driver"')
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.user, action="viewed", resource_type="DayBoard", resource_id="2027-03-10"
            ).exists()
        )

    def test_quick_assign(self):
        trip = direct_trip(self.passenger, scheduled_pickup_datetime=self.at(8))
        trip.save()
        response = self.client.post(
            reverse("admin:trips_trip_board_assign", args=[trip.pk]),
            {"driver": str(self.driver.pk), "vehicle": str(self.vehicle.pk)},
        )
        self.assertRedirects(
            response, reverse("admin:trips_trip_board") + "?date=2027-03-10", fetch_redirect_response=False
        )
        trip.refresh_from_db()
        self.assertEqual((trip.driver, trip.vehicle), (self.driver, self.vehicle))
        entry = AuditLog.objects.get(action="assigned", resource_id=str(trip.pk))
        self.assertEqual(entry.after, {"driver": str(self.driver.pk), "vehicle": str(self.vehicle.pk)})

    def test_quick_assign_still_blocks_expired_credentials(self):
        expired = make_driver(license_number="X", license_expiration_date=date.today() - timedelta(days=1))
        trip = direct_trip(self.passenger, scheduled_pickup_datetime=self.at(8))
        trip.save()
        response = self.client.post(
            reverse("admin:trips_trip_board_assign", args=[trip.pk]),
            {"driver": str(expired.pk), "vehicle": str(self.vehicle.pk)},
            follow=True,
        )
        self.assertContains(response, "Couldn&#x27;t assign")
        trip.refresh_from_db()
        self.assertIsNone(trip.driver)

    def test_staff_without_trip_access_are_refused(self):
        User.objects.create_user(username="nobody", password="testpass123", is_staff=True)
        self.client.logout()
        login_with_otp(self.client, username="nobody", password="testpass123")
        self.assertEqual(self.client.get(reverse("admin:trips_trip_board")).status_code, 403)
        trip = direct_trip(self.passenger)
        trip.save()
        response = self.client.post(
            reverse("admin:trips_trip_board_assign", args=[trip.pk]),
            {"driver": str(self.driver.pk), "vehicle": str(self.vehicle.pk)},
        )
        self.assertEqual(response.status_code, 403)


class TurnDownTests(TestCase):
    def test_dispatcher_logs_a_turn_down_and_is_recorded_as_the_author(self):
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")

        response = self.client.post(
            reverse("admin:trips_turndown_add"),
            {
                "source": "saferide",
                "broker_trip_number": "SR-42",
                "offered_at_0": "2027-03-10",
                "offered_at_1": "09:00:00",
                "reason": "no_vehicle",
                "service_type": "stretcher",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        turn_down = TurnDown.objects.get()
        self.assertEqual(turn_down.recorded_by, user)
        self.assertEqual(str(turn_down), "SafeRide SR-42: No suitable vehicle (e.g. wheelchair / stretcher)")

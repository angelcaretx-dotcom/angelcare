"""SLA dashboard (ADR 0037): on-time, no-shows, acceptance and turn-downs per payer, per week."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from payers.models import Payer

from .models import Trip, TripEvent, TripLeg, TripSource, TripStatus, TurnDown
from .sla import build_sla, week_of, week_rows
from .tests import make_passenger

CENTRAL = ZoneInfo("America/Chicago")
MONDAY = date(2027, 1, 11)


def at(day_offset, hour, minute=0):
    return datetime.combine(MONDAY + timedelta(days=day_offset), time(hour, minute), tzinfo=CENTRAL)


class SlaTestCase(TestCase):
    def setUp(self):
        self.passenger = make_passenger()
        self.mtm = Payer.objects.create(name="MTM", payer_type="broker")

    def trip(self, pickup, *, arrived_after=None, status=TripStatus.COMPLETED, payer=None, **extra):
        trip = Trip.objects.create(
            passenger=self.passenger,
            source=extra.pop("source", TripSource.MTM),
            payer=payer if payer is not None else self.mtm,
            service_type="ambulatory",
            pickup_address="1 Main St",
            dropoff_address="2 Clinic Rd",
            scheduled_pickup_datetime=pickup,
            status=status,
            **extra,
        )
        Trip.objects.filter(pk=trip.pk).update(created_at=pickup - timedelta(hours=1))  # offered that week
        if arrived_after is not None:
            TripEvent.objects.create(trip=trip, event_type="arrived_pickup", occurred_at=pickup + arrived_after)
        return trip


class OnTimeTests(SlaTestCase):
    def test_fifteen_minutes_late_is_on_time_sixteen_is_late(self):
        self.trip(at(0, 9), arrived_after=timedelta(minutes=-5))
        self.trip(at(1, 9), arrived_after=timedelta(minutes=15))
        self.trip(at(2, 9), arrived_after=timedelta(minutes=16))
        rows, total = week_rows(MONDAY)
        self.assertEqual((total.measured, total.on_time, total.late), (3, 2, 1))
        self.assertEqual(total.on_time_percent, 66.7)
        self.assertTrue(total.on_time_low)

    def test_will_call_cancelled_and_untapped_trips(self):
        self.trip(at(0, 9), arrived_after=timedelta(hours=3), will_call=True, leg=TripLeg.RETURN)
        self.trip(at(1, 9), status=TripStatus.CANCELLED)
        self.trip(at(2, 9))  # completed but the driver never tapped arrival
        _, total = week_rows(MONDAY)
        self.assertEqual((total.measured, total.not_measured, total.cancelled), (0, 1, 1))
        self.assertIsNone(total.on_time_percent)

    def test_picked_up_counts_when_arrival_was_skipped(self):
        trip = self.trip(at(0, 9))
        TripEvent.objects.create(trip=trip, event_type="picked_up", occurred_at=at(0, 9, 10))
        _, total = week_rows(MONDAY)
        self.assertEqual(total.on_time, 1)

    def test_other_weeks_are_left_out(self):
        self.trip(at(-1, 9), arrived_after=timedelta(minutes=40))  # the Sunday before
        self.trip(at(7, 9), arrived_after=timedelta(minutes=40))  # next Monday
        self.assertEqual(week_rows(MONDAY)[1].trips, 0)


class AcceptanceTests(SlaTestCase):
    def test_acceptance_per_payer_with_turn_downs_by_reason(self):
        for day in range(19):
            self.trip(at(day % 7, 8) if day < 7 else at(day % 7, 10 + day % 3), arrived_after=timedelta(0))
        TurnDown.objects.create(source=TripSource.MTM, payer=self.mtm, reason="no_driver", offered_at=at(2, 7))
        TurnDown.objects.create(source=TripSource.SAFERIDE, reason="outside_area", offered_at=at(3, 7))
        rows, total = week_rows(MONDAY)
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["MTM"].accepted, 19)
        self.assertEqual(by_name["MTM"].acceptance_percent, 95.0)
        self.assertFalse(by_name["MTM"].acceptance_low)
        self.assertEqual(by_name["SafeRide"].acceptance_percent, 0.0)  # no payer: grouped by source
        self.assertEqual(total.turned_down, 2)
        data = build_sla(MONDAY)
        self.assertEqual(dict(data["reasons"]), {"No driver available": 1, "Outside service area": 1})

    def test_return_legs_and_standing_order_trips_are_not_offers(self):
        outbound = self.trip(at(0, 8))
        self.trip(at(0, 12), leg=TripLeg.RETURN, outbound_trip=outbound)
        self.assertEqual(week_rows(MONDAY)[1].accepted, 1)

    def test_no_show_rate(self):
        self.trip(at(0, 9), arrived_after=timedelta(0))
        self.trip(at(1, 9), arrived_after=timedelta(0), status=TripStatus.NO_SHOW)
        _, total = week_rows(MONDAY)
        self.assertEqual((total.no_shows, total.no_show_percent), (1, 50.0))


class SlaPageTests(SlaTestCase):
    def setUp(self):
        super().setUp()
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")

    def test_week_page_shows_payers_red_below_target_and_the_trend(self):
        self.trip(at(0, 9), arrived_after=timedelta(minutes=30))
        page = self.client.get("/admin/trips/trip/sla/", {"week": "2027-01-13"}).content.decode()
        self.assertIn('data-sla="2027-01-11"', page)  # any day picks its week
        self.assertIn('data-sla-row="MTM"', page)
        self.assertIn("0.0%", page)
        self.assertIn("text-red-600", page)
        self.assertIn("Last 8 weeks", page)

    def test_needs_trip_view_permission(self):
        User.objects.create_user(username="nobody", password="testpass123", is_staff=True)
        self.client.logout()
        login_with_otp(self.client, username="nobody", password="testpass123")
        self.assertEqual(self.client.get("/admin/trips/trip/sla/").status_code, 403)

    def test_week_of(self):
        self.assertEqual(week_of(date(2027, 1, 17)), MONDAY)

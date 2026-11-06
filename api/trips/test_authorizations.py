"""Prior authorizations (ADR 0043): trips used vs. remaining, the block, and the alerts."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.dashboard import build_dashboard
from accounts.otp_test_utils import login_with_otp
from payers.models import Payer
from transportation.models import ReturnRide

from . import authorizations, standing
from .models import AuthorizationUnit, PriorAuthorization, Trip, TripLeg, TripStatus
from .test_standing import make_order
from .tests import make_passenger

CENTRAL = ZoneInfo("America/Chicago")
TODAY = standing.texas_today()


def at(day, hour=9):
    return datetime.combine(day, time(hour), tzinfo=CENTRAL)


class AuthorizationTestCase(TestCase):
    def setUp(self):
        self.payer = Payer.objects.create(name="Texas Medicaid (TMHP)", payer_type="medicaid_mco")
        self.rider = make_passenger(payer=self.payer)
        self.auth = PriorAuthorization.objects.create(
            number="PA-1001", passenger=self.rider, payer=self.payer, valid_from=TODAY - timedelta(days=30),
            valid_to=TODAY + timedelta(days=60), trips_authorized=4,
        )

    def trip(self, *, save=True, day=None, number="PA-1001", **extra):
        trip = Trip(
            passenger=self.rider, payer=self.payer, service_type="wheelchair", pickup_address="1 Main St",
            dropoff_address="2 Clinic Rd", scheduled_pickup_datetime=at(day or TODAY + timedelta(days=1)),
            prior_authorization_number=number, **extra,
        )
        if save:
            trip.save()
        return trip


class CountingTests(AuthorizationTestCase):
    def test_each_leg_counts_unless_cancelled(self):
        out = self.trip(leg=TripLeg.OUTBOUND)
        self.trip(leg=TripLeg.RETURN, outbound_trip=out)
        self.trip(status=TripStatus.CANCELLED)
        self.trip(status=TripStatus.NO_SHOW)
        self.assertEqual((authorizations.used(self.auth), authorizations.remaining(self.auth)), (3, 1))

    def test_round_trip_authorizations_count_there_and_back_once(self):
        self.auth.counts = AuthorizationUnit.ROUND_TRIP
        self.auth.save()
        out = self.trip(leg=TripLeg.OUTBOUND)
        self.trip(leg=TripLeg.RETURN, outbound_trip=out)
        self.assertEqual(authorizations.used(self.auth), 1)

    def test_number_matching_ignores_case(self):
        self.trip(number="pa-1001")
        self.assertEqual(authorizations.used(self.auth), 1)


class BlockTests(AuthorizationTestCase):
    def test_no_trip_beyond_the_authorized_number(self):
        for _ in range(4):
            self.trip()
        with self.assertRaisesMessage(ValidationError, "All 4 trips on authorization PA-1001 are used"):
            self.trip(save=False).full_clean()
        existing = Trip.objects.first()
        existing.notes = "gate code 1234"
        existing.full_clean()  # trips already booked under it keep working

    def test_cancelling_one_frees_its_place(self):
        trips = [self.trip() for _ in range(4)]
        trips[0].status = TripStatus.CANCELLED
        trips[0].save()
        self.trip(save=False).full_clean()

    def test_outside_its_dates_or_for_someone_else_is_blocked(self):
        with self.assertRaisesMessage(ValidationError, "covers"):
            self.trip(save=False, day=TODAY + timedelta(days=61)).full_clean()
        other = self.trip(save=False)
        other.passenger = make_passenger(legal_name="Someone Else", payer=self.payer)
        with self.assertRaisesMessage(ValidationError, "another rider"):
            other.full_clean()

    def test_numbers_not_entered_are_not_tracked(self):
        self.trip(save=False, number="UNKNOWN-9").full_clean()


class StandingOrderTests(AuthorizationTestCase):
    def test_standing_order_stops_when_the_authorization_runs_out(self):
        order = make_order(passenger=self.rider, payer=self.payer, prior_authorization_number="PA-1001",
                           weekdays=[0, 1, 2, 3, 4, 5, 6], return_ride=ReturnRide.SCHEDULED,
                           return_time=time(11, 0), pickup_time=time(6, 0))
        made = standing.generate(order)
        self.assertEqual(len(made), 4)  # two days, there and back
        self.assertEqual(authorizations.remaining(self.auth), 0)


class AlertTests(AuthorizationTestCase):
    def test_low_then_used_up_emails_once_each_without_numbers_or_names(self):
        self.auth.trips_authorized = 10
        self.auth.save()
        self.assertEqual(authorizations.send_alerts(TODAY), 0)
        for _ in range(7):
            self.trip()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(authorizations.send_alerts(TODAY), 1)
        email = mail.outbox[-1]
        self.assertIn("1 prior authorization running low", email.subject)
        self.assertIn("3 of 10 trips left", email.body)
        for private in ("PA-1001", self.rider.legal_name):
            self.assertNotIn(private, email.subject + email.body)
        self.assertEqual(authorizations.send_alerts(TODAY), 0)  # once per stage
        for _ in range(3):
            self.trip()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(authorizations.send_alerts(TODAY), 1)
        self.assertIn("used up", mail.outbox[-1].body)

    def test_dashboard_panel(self):
        admin = User.objects.create_user(username="admin1", password="x", is_staff=True)
        admin.groups.add(Group.objects.get(name="Administrator"))
        for _ in range(3):
            self.trip()
        panel = next(p for p in build_dashboard(admin).panels if p.title == "Prior authorizations running low")
        self.assertEqual(panel.rows[0].cells[2], "1 of 4")


class AdminTests(AuthorizationTestCase):
    def test_pages_show_usage(self):
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        trip = self.trip()
        self.assertContains(self.client.get(f"/admin/trips/priorauthorization/{self.auth.pk}/change/"),
                            "1 of 4 (3 left)")
        self.assertContains(self.client.get(f"/admin/trips/trip/{trip.pk}/change/"), "1 of 4 used, 3 left")
        self.assertEqual(self.client.get("/admin/trips/priorauthorization/").status_code, 200)

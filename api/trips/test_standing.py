"""Standing orders (ADR 0035): repeating rides turned into trips, 14 days ahead."""

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from transportation.models import ReturnRide

from . import standing
from .models import StandingOrder, StandingOrderSkip, Trip, TripLeg, TripStatus
from .tests import make_driver, make_passenger, make_vehicle

CENTRAL = ZoneInfo("America/Chicago")
TODAY = standing.texas_today()
TOMORROW = TODAY + timedelta(days=1)


def make_order(**overrides) -> StandingOrder:
    defaults = {
        "passenger": make_passenger(),
        "service_type": "wheelchair",
        "pickup_address": "1 Main St, Fort Worth, TX 76104",
        "dropoff_address": "2 Dialysis Way, Fort Worth, TX 76104",
        "weekdays": [0, 2, 4],
        "pickup_time": time(6, 0),
        "start_date": TOMORROW,
    }
    defaults.update(overrides)
    order = StandingOrder(**defaults)
    order.full_clean()
    order.save()
    return order


def expected_days(weekdays, first=TOMORROW, last=None):
    last = last or TODAY + timedelta(days=standing.HORIZON_DAYS - 1)
    days, day = [], first
    while day <= last:
        if day.weekday() in weekdays:
            days.append(day)
        day += timedelta(days=1)
    return days


class GenerateTests(TestCase):
    def test_mon_wed_fri_for_the_next_14_days_at_texas_time(self):
        order = make_order()
        made = standing.generate(order)
        self.assertEqual(sorted(t.standing_date for t in made), expected_days([0, 2, 4]))
        trip = made[0]
        local = timezone.localtime(trip.scheduled_pickup_datetime, CENTRAL)
        self.assertEqual((local.hour, local.minute), (6, 0))
        self.assertEqual((trip.leg, trip.status, trip.standing_order), (TripLeg.ONE_WAY, TripStatus.SCHEDULED, order))
        self.assertEqual(trip.dropoff_address, "2 Dialysis Way, Fort Worth, TX 76104")
        self.assertTrue(AuditLog.objects.filter(action="created", source="standing_order").exists())

    def test_running_again_makes_nothing_new(self):
        order = make_order()
        first = standing.generate(order)
        self.assertEqual(standing.generate(order), [])
        self.assertEqual(Trip.objects.count(), len(first))

    def test_ride_back_at_a_set_time(self):
        order = make_order(return_ride=ReturnRide.SCHEDULED, return_time=time(10, 30), weekdays=[TOMORROW.weekday()])
        made = standing.generate(order)
        outbound = next(t for t in made if t.leg == TripLeg.OUTBOUND)
        back = next(t for t in made if t.leg == TripLeg.RETURN)
        self.assertEqual(back.outbound_trip, outbound)
        self.assertEqual(back.pickup_address, outbound.dropoff_address)
        self.assertEqual(timezone.localtime(back.scheduled_pickup_datetime, CENTRAL).time(), time(10, 30))

    def test_will_call_and_wait_and_return(self):
        will_call = make_order(return_ride=ReturnRide.WILL_CALL, weekdays=[TOMORROW.weekday()])
        back = next(t for t in standing.generate(will_call) if t.leg == TripLeg.RETURN)
        self.assertTrue(back.will_call)

        driver, vehicle = make_driver(license_number="W1", phone="817-555-0101"), make_vehicle(vin="1FTEW1EP0MFA00009")
        waits = make_order(
            return_ride=ReturnRide.WAIT, weekdays=[TOMORROW.weekday()], driver=driver, vehicle=vehicle,
            passenger=make_passenger(legal_name="Lee Wait", phone="817-555-0177"),
        )
        back = next(t for t in standing.generate(waits) if t.leg == TripLeg.RETURN)
        self.assertTrue(back.driver_waits)
        self.assertEqual((back.driver, back.vehicle), (driver, vehicle))

    def test_regular_driver_and_vehicle_when_available(self):
        driver, vehicle = make_driver(), make_vehicle()
        order = make_order(driver=driver, vehicle=vehicle)
        trip = standing.generate(order)[0]
        self.assertEqual((trip.driver, trip.vehicle), (driver, vehicle))

    def test_unavailable_regulars_leave_the_trip_unassigned_for_dispatch(self):
        driver = make_driver(status="inactive")
        vehicle = make_vehicle(status="out_of_service")
        order = make_order(driver=driver, vehicle=vehicle)
        made = standing.generate(order)
        self.assertTrue(made)
        self.assertTrue(all(t.driver_id is None and t.vehicle_id is None for t in made))

    def test_skipped_dates_end_dates_and_paused_orders(self):
        order = make_order(weekdays=list(range(7)), end_date=TOMORROW + timedelta(days=3))
        StandingOrderSkip.objects.create(order=order, date=TOMORROW + timedelta(days=1), reason="Holiday")
        days = sorted(t.standing_date for t in standing.generate(order))
        self.assertEqual(days, [TOMORROW, TOMORROW + timedelta(days=2), TOMORROW + timedelta(days=3)])
        paused = make_order(active=False, passenger=make_passenger(legal_name="Paused", phone="817-555-0122"))
        self.assertEqual(standing.generate(paused), [])

    def test_no_trips_after_the_prior_authorization_ends(self):
        order = make_order(weekdays=list(range(7)), prior_authorization_ends=TOMORROW + timedelta(days=1))
        days = sorted(t.standing_date for t in standing.generate(order))
        self.assertEqual(days, [TOMORROW, TOMORROW + timedelta(days=1)])

    def test_order_rules(self):
        with self.assertRaises(ValidationError):
            make_order(weekdays=[])
        with self.assertRaises(ValidationError):
            make_order(return_ride=ReturnRide.SCHEDULED)  # no time
        with self.assertRaises(ValidationError):
            make_order(return_ride=ReturnRide.SCHEDULED, return_time=time(5, 0))  # before pickup
        with self.assertRaises(ValidationError):
            make_order(end_date=TODAY - timedelta(days=5))


class RefreshTests(TestCase):
    def test_changed_order_remakes_untouched_trips_and_keeps_edited_ones(self):
        order = make_order(weekdays=list(range(7)))
        made = standing.generate(order)
        edited = sorted(made, key=lambda t: t.standing_date)[0]
        Trip.objects.filter(pk=edited.pk).update(
            notes="Moved by dispatch", updated_at=edited.created_at + timedelta(minutes=5)
        )
        order.pickup_time = time(7, 15)
        order.save()

        remade, kept = standing.refresh(order)
        self.assertEqual(kept, 1)
        self.assertEqual(len(remade), len(made) - 1)
        self.assertEqual(Trip.objects.get(pk=edited.pk).notes, "Moved by dispatch")
        self.assertTrue(
            all(timezone.localtime(t.scheduled_pickup_datetime, CENTRAL).time() == time(7, 15) for t in remade)
        )
        self.assertEqual(Trip.objects.filter(standing_order=order).count(), len(made))

    def test_skipping_a_date_after_trips_exist(self):
        order = make_order(weekdays=list(range(7)), return_ride=ReturnRide.WILL_CALL)
        standing.generate(order)
        StandingOrderSkip.objects.create(order=order, date=TOMORROW)
        standing.refresh(order)
        self.assertFalse(Trip.objects.filter(standing_order=order, standing_date=TOMORROW).exists())

    def test_edited_trip_on_a_skipped_date_is_cancelled_not_deleted(self):
        order = make_order(weekdays=list(range(7)))
        trip = next(t for t in standing.generate(order) if t.standing_date == TOMORROW)
        Trip.objects.filter(pk=trip.pk).update(updated_at=trip.created_at + timedelta(minutes=5))
        StandingOrderSkip.objects.create(order=order, date=TOMORROW)
        standing.refresh(order)
        self.assertEqual(Trip.objects.get(pk=trip.pk).status, TripStatus.CANCELLED)


class DailyJobTests(TestCase):
    def test_tops_up_and_warns_once_about_an_ending_authorization(self):
        order = make_order(prior_authorization_ends=TODAY + timedelta(days=10), prior_authorization_number="PA-77")
        result = standing.run_daily()
        self.assertGreater(result["trips_created"], 0)
        self.assertEqual(result["auth_warnings"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(order.reference, mail.outbox[0].subject)
        self.assertNotIn(order.passenger.legal_name, mail.outbox[0].body)
        self.assertEqual(standing.run_daily(), {"trips_created": 0, "orders_failed": 0, "auth_warnings": 0})
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(CRON_SECRET="")
    def test_cron_endpoint_refuses_without_a_secret(self):
        self.assertEqual(self.client.get("/api/v1/cron/standing-orders/").status_code, 503)

    @override_settings(CRON_SECRET="s3cret")
    def test_cron_endpoint_needs_the_right_secret(self):
        make_order()
        url = "/api/v1/cron/standing-orders/"
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION="Bearer wrong").status_code, 403)
        response = self.client.get(url, HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()["trips_created"], 0)


class StandingOrderAdminTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        self.passenger = make_passenger()

    def form(self, **overrides):
        data = {
            "passenger": str(self.passenger.pk),
            "service_type": "wheelchair",
            "pickup_address": "1 Main St",
            "dropoff_address": "2 Dialysis Way",
            "weekdays": ["0", "2", "4"],
            "pickup_time": "06:00",
            "appointment_time": "",
            "return_ride": "will_call",
            "return_time": "",
            "start_date": TOMORROW.isoformat(),
            "end_date": "",
            "active": "on",
            "source": "phone",
            "subcontract_partner": "",
            "payer": "",
            "prior_authorization_number": "",
            "prior_authorization_ends": "",
            "driver": "",
            "vehicle": "",
            "notes": "",
            "skips-TOTAL_FORMS": "0",
            "skips-INITIAL_FORMS": "0",
        }
        data.update(overrides)
        return data

    def test_dispatcher_saves_an_order_and_trips_appear(self):
        response = self.client.post("/admin/trips/standingorder/add/", self.form(), follow=True)
        self.assertContains(response, "Trips created for")
        order = StandingOrder.objects.get()
        self.assertEqual(order.weekdays, [0, 2, 4])
        days = expected_days([0, 2, 4])
        self.assertEqual(Trip.objects.filter(standing_order=order, leg=TripLeg.OUTBOUND).count(), len(days))
        self.assertEqual(Trip.objects.filter(standing_order=order, leg=TripLeg.RETURN).count(), len(days))
        page = self.client.get(f"/admin/trips/standingorder/{order.pk}/change/")
        self.assertContains(page, "Upcoming trips")
        self.assertTrue(AuditLog.objects.filter(action="viewed", resource_type="StandingOrder").exists())
        trip = Trip.objects.filter(standing_order=order).first()
        self.assertContains(self.client.get(f"/admin/trips/trip/{trip.pk}/change/"), order.reference)

    def test_authorization_warning_shows_on_the_list(self):
        make_order(passenger=self.passenger, prior_authorization_ends=TODAY + timedelta(days=5))
        self.assertContains(self.client.get("/admin/trips/standingorder/"), "renew soon")

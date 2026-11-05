"""Phase 25 (ADR 0034): daily vehicle check, odometer, signature at pickup, proof sheet."""

from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from trips.models import TripStatus
from trips.test_dispatch import direct_trip
from trips.tests import make_passenger, make_vehicle
from vehicles import checks
from vehicles.models import CHECK_ITEMS, VehicleCheck, VehicleStatus, check_items_for

from .tests import PASSWORD, checked_vehicle, driver_with_login, signature_data_url


def all_ok(vehicle):
    return [key for key, _ in check_items_for(vehicle)]


class DriverAppTestCase(TestCase):
    def setUp(self):
        self.driver = driver_with_login()
        self.client.post(reverse("driver_app:login"), {"identifier": "sam@example.com", "password": PASSWORD})
        self.passenger = make_passenger(legal_name="Morgan Lee")

    def trip(self, vehicle, **overrides):
        defaults = {"driver": self.driver, "vehicle": vehicle, "scheduled_pickup_datetime": timezone.now()}
        defaults.update(overrides)
        trip = direct_trip(self.passenger, **defaults)
        trip.save()
        return trip

    def step(self, trip, event_type, **extra):
        data = {"event_type": event_type, "latitude": "32.7555", "longitude": "-97.3308", "accuracy": "12"}
        data.update(extra)
        return self.client.post(reverse("driver_app:step", args=[trip.pk]), data)


class DailyVehicleCheckTests(DriverAppTestCase):
    def setUp(self):
        super().setUp()
        self.vehicle = make_vehicle(wheelchair_capable=True)
        self.trip_today = self.trip(self.vehicle)

    def check(self, ok, odometer="48213", notes=""):
        return self.client.post(
            reverse("driver_app:vehicle_check", args=[self.vehicle.pk]),
            {"ok": ok, "odometer": odometer, "notes": notes},
        )

    def test_no_trip_starts_before_the_check(self):
        home = self.client.get(reverse("driver_app:home"))
        self.assertContains(home, "Start of day: check")
        self.assertContains(self.client.get(reverse("driver_app:trip", args=[self.trip_today.pk])), "daily vehicle check")
        self.step(self.trip_today, "en_route")
        self.assertFalse(self.trip_today.events.exists())

    def test_wheelchair_van_gets_the_lift_and_tie_down_items(self):
        page = self.client.get(reverse("driver_app:vehicle_check", args=[self.vehicle.pk]))
        self.assertContains(page, "Wheelchair lift or ramp works")
        self.assertNotContains(page, "Stretcher and its locks")
        self.assertEqual(len(check_items_for(self.vehicle)), len(CHECK_ITEMS) + 2)

    def test_passed_check_lets_trips_start(self):
        response = self.check(all_ok(self.vehicle))
        self.assertRedirects(response, reverse("driver_app:home"))
        check = VehicleCheck.objects.get()
        self.assertTrue(check.passed)
        self.assertEqual((check.start_odometer, check.driver), (48213, self.driver))
        self.step(self.trip_today, "en_route")
        self.assertEqual(self.trip_today.events.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(AuditLog.objects.filter(action="vehicle_checked").exists())

    def test_once_per_vehicle_per_day(self):
        self.check(all_ok(self.vehicle))
        with self.assertRaises(checks.VehicleCheckError):
            checks.record_start(
                self.vehicle, self.driver, ok_items=all_ok(self.vehicle), odometer="48300", notes="", user=None
            )

    def test_failed_item_needs_a_note_then_takes_the_vehicle_out_of_service(self):
        failing = [k for k in all_ok(self.vehicle) if k != "brakes"]
        self.check(failing)
        self.assertFalse(VehicleCheck.objects.exists())

        with self.captureOnCommitCallbacks(execute=True):  # the alert goes out after saving
            self.check(failing, notes="Brake pedal soft")
        check = VehicleCheck.objects.get()
        self.assertEqual(check.failed_items, ["brakes"])
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, VehicleStatus.OUT_OF_SERVICE)
        # Dispatch is told, without rider details.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.vehicle.license_plate, mail.outbox[0].subject)
        self.assertNotIn("Morgan", mail.outbox[0].body)
        # No trip can start in it, or be assigned to it.
        self.step(self.trip_today, "en_route")
        self.assertFalse(self.trip_today.events.exists())
        with self.assertRaises(ValidationError):
            direct_trip(self.passenger, vehicle=self.vehicle).full_clean()
        self.assertContains(self.client.get(reverse("driver_app:home")), "is out of service")

    def test_odometer_cannot_go_backwards(self):
        VehicleCheck.objects.create(
            vehicle=self.vehicle, driver=self.driver, service_date=checks.texas_today() - timedelta(days=1),
            start_odometer=48000, end_odometer=48150,
        )
        self.check(all_ok(self.vehicle), odometer="48100")
        self.assertFalse(VehicleCheck.objects.filter(service_date=checks.texas_today()).exists())
        self.check(all_ok(self.vehicle), odometer="48,150")
        self.assertTrue(VehicleCheck.objects.filter(service_date=checks.texas_today()).exists())

    def test_end_of_day_odometer_gives_the_days_miles(self):
        self.check(all_ok(self.vehicle), odometer="48213")
        check = VehicleCheck.objects.get()
        url = reverse("driver_app:vehicle_day_end", args=[check.pk])
        self.client.post(url, {"odometer": "48100"})  # lower than this morning
        self.client.post(url, {"odometer": "50000"})  # over 1,000 miles
        check.refresh_from_db()
        self.assertIsNone(check.end_odometer)
        response = self.client.post(url, {"odometer": "48377"})
        self.assertRedirects(response, reverse("driver_app:home"))
        check.refresh_from_db()
        self.assertEqual(check.miles, 164)
        self.assertContains(self.client.get(reverse("driver_app:home")), "Day done · 164 mi")

    def test_yesterdays_missing_end_odometer_is_asked_for(self):
        old = VehicleCheck.objects.create(
            vehicle=self.vehicle, driver=self.driver, service_date=checks.texas_today() - timedelta(days=1),
            start_odometer=48000,
        )
        self.assertContains(self.client.get(reverse("driver_app:home")), "End-of-day odometer missing")
        self.client.post(reverse("driver_app:vehicle_day_end", args=[old.pk]), {"odometer": "48100"})
        old.refresh_from_db()
        self.assertEqual(old.miles, 100)

    def test_cannot_check_a_vehicle_not_on_my_trips_today(self):
        other = make_vehicle(vin="1FTEW1EP0MFA00002", license_plate="OTHER1")
        self.assertEqual(self.client.get(reverse("driver_app:vehicle_check", args=[other.pk])).status_code, 404)


class ClearingAFailedCheckTests(TestCase):
    def setUp(self):
        self.driver = driver_with_login()
        self.vehicle = make_vehicle()
        self.check = checks.record_start(
            self.vehicle, self.driver, ok_items=[], odometer="1000", notes="Everything wrong", user=None
        )
        self.url = reverse("admin:vehicles_vehiclecheck_change", args=[self.check.pk])

    def staff(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")

    def test_administrator_marks_it_fixed_and_the_vehicle_is_back_in_service(self):
        self.staff("Administrator")
        self.assertContains(self.client.get(self.url), "Failed — out of service")
        response = self.client.post(self.url, {"cleared_note": "Replaced brake pads"})
        self.assertEqual(response.status_code, 302)
        self.check.refresh_from_db()
        self.vehicle.refresh_from_db()
        self.assertEqual(self.check.cleared_note, "Replaced brake pads")
        self.assertFalse(self.check.blocks_vehicle)
        self.assertEqual(self.vehicle.status, VehicleStatus.ACTIVE)
        self.assertTrue(AuditLog.objects.filter(action="vehicle_check_cleared").exists())

    def test_dispatcher_can_read_but_not_clear(self):
        self.staff("Dispatcher")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.post(self.url, {"cleared_note": "Looks fine"})
        self.check.refresh_from_db()
        self.assertTrue(self.check.blocks_vehicle)

    def test_vehicle_put_in_maintenance_for_another_reason_stays_there(self):
        self.vehicle.status = VehicleStatus.MAINTENANCE
        self.vehicle.save()
        checks.clear(self.check, note="Fixed", user=None)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, VehicleStatus.MAINTENANCE)


class SignatureAtPickupTests(DriverAppTestCase):
    def setUp(self):
        super().setUp()
        self.trip_today = self.trip(checked_vehicle(self.driver))
        self.step(self.trip_today, "arrived_pickup")

    def test_pickup_needs_a_signature_or_a_reason(self):
        self.step(self.trip_today, "picked_up")
        self.assertEqual(self.trip_today.events.count(), 1)
        self.step(self.trip_today, "picked_up", signature_missing_reason="other")  # "other" needs a note
        self.assertEqual(self.trip_today.events.count(), 1)
        self.step(self.trip_today, "picked_up", signature_missing_reason="unable")
        pickup = self.trip_today.events.last()
        self.assertEqual((pickup.event_type, pickup.signature_missing_reason), ("picked_up", "unable"))
        self.assertIsNone(pickup.signature)

    def test_signature_is_stored_as_a_small_png(self):
        self.step(self.trip_today, "picked_up", signature=signature_data_url(), signed_by_name="Pat Lee (daughter)")
        pickup = self.trip_today.events.last()
        self.assertEqual(bytes(pickup.signature)[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(pickup.signed_by_name, "Pat Lee (daughter)")
        self.assertContains(self.client.get(reverse("driver_app:trip", args=[self.trip_today.pk])), "signed")

    def test_empty_or_bogus_signature_is_refused(self):
        import base64
        import io

        from PIL import Image

        blank = io.BytesIO()
        Image.new("RGBA", (300, 120), (0, 0, 0, 0)).save(blank, format="PNG")
        for bad in ("data:image/png;base64," + base64.b64encode(blank.getvalue()).decode(),
                    "data:image/png;base64,bm90IGEgcG5n", "javascript:alert(1)"):
            self.step(self.trip_today, "picked_up", signature=bad)
        self.assertEqual(self.trip_today.events.count(), 1)

    def test_signature_shown_on_the_pickup_step_only(self):
        self.step(self.trip_today, "picked_up", signature=signature_data_url())
        self.step(self.trip_today, "dropped_off", signature=signature_data_url())
        self.trip_today.refresh_from_db()
        self.assertEqual(self.trip_today.status, TripStatus.COMPLETED)
        self.assertIsNone(self.trip_today.events.last().signature)


class ProofSheetTests(DriverAppTestCase):
    def test_pdf_built_on_demand_with_signature_and_vehicle_day(self):
        vehicle = checked_vehicle(self.driver)
        trip = self.trip(vehicle, broker_trip_number="MTM-123")
        for step in ("arrived_pickup", "picked_up", "dropped_off"):
            self.step(trip, step, signature=signature_data_url(), note="Rider used a walker — fine")
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")

        change = self.client.get(reverse("admin:trips_trip_change", args=[trip.pk]))
        self.assertContains(change, "Proof sheet (PDF)")
        self.assertContains(change, "data:image/png;base64,")
        response = self.client.get(reverse("admin:trips_trip_proof", args=[trip.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 2000)
        self.assertTrue(
            AuditLog.objects.filter(action="exported", resource_type="Trip", resource_id=str(trip.pk)).exists()
        )

    def test_trip_with_no_steps_still_gets_a_sheet(self):
        trip = self.trip(make_vehicle())
        user = User.objects.create_superuser(username="owner", password="testpass123", email="o@example.com")
        login_with_otp(self.client, username=user.username, password="testpass123")
        response = self.client.get(reverse("admin:trips_trip_proof", args=[trip.pk]))
        self.assertTrue(response.content.startswith(b"%PDF"))

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.onboarding import must_change_password
from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from trips.events import TripEventError, record_trip_event
from trips.models import Trip, TripEvent, TripStatus
from trips.test_dispatch import direct_trip
from trips.tests import make_driver, make_passenger, make_vehicle

from . import views
from .access import DriverAccessError, setup_driver_login
from .auth import MAX_FAILURES, find_driver, normalize_phone

PASSWORD = "Ride-safe-2027!"


def driver_with_login(**overrides):
    defaults = {"email": "sam@example.com", "phone": "(817) 555-0155"}
    defaults.update(overrides)
    driver = make_driver(**defaults)
    setup_driver_login(driver, actor=None)
    driver.refresh_from_db()
    driver.user.set_password(PASSWORD)
    driver.user.save()
    driver.user.onboarding.must_change_password = False
    driver.user.onboarding.save()
    return driver


class IdentifierTests(TestCase):
    def test_phone_formats_normalize_to_ten_digits(self):
        for raw in ("(817) 555-0155", "817.555.0155", "+1 817 555 0155", "18175550155"):
            self.assertEqual(normalize_phone(raw), "8175550155")

    def test_finds_driver_by_email_or_any_phone_format(self):
        driver = driver_with_login()
        self.assertEqual(find_driver("SAM@example.com"), driver)
        self.assertEqual(find_driver("817-555-0155"), driver)
        self.assertIsNone(find_driver("nobody@example.com"))
        self.assertIsNone(find_driver("555"))


class SetupLoginTests(TestCase):
    def test_creates_a_non_staff_login_that_must_change_its_password(self):
        driver = make_driver(email="sam@example.com")
        password = setup_driver_login(driver, actor=None)
        driver.refresh_from_db()
        self.assertFalse(driver.user.is_staff)
        self.assertTrue(driver.user.check_password(password))
        self.assertTrue(must_change_password(driver.user))
        self.assertTrue(AuditLog.objects.filter(action="driver_login_created").exists())

    def test_reset_replaces_the_password(self):
        driver = driver_with_login()
        new = setup_driver_login(driver, actor=None)
        driver.user.refresh_from_db()
        self.assertTrue(driver.user.check_password(new))
        self.assertFalse(driver.user.check_password(PASSWORD))
        self.assertTrue(must_change_password(driver.user))

    def test_refuses_inactive_drivers_and_shared_phone_numbers(self):
        with self.assertRaises(DriverAccessError):
            setup_driver_login(make_driver(status="inactive", license_number="A"), actor=None)
        make_driver(phone="817-555-0199", license_number="B")
        with self.assertRaises(DriverAccessError):
            setup_driver_login(make_driver(phone="8175550199", license_number="C"), actor=None)


class SignInTests(TestCase):
    def setUp(self):
        self.driver = driver_with_login()
        self.url = reverse("driver_app:login")

    def sign_in(self, identifier, password=PASSWORD):
        return self.client.post(self.url, {"identifier": identifier, "password": password})

    def test_sign_in_with_email_or_phone(self):
        for identifier in ("sam@example.com", "817 555 0155"):
            with self.subTest(identifier=identifier):
                self.assertRedirects(self.sign_in(identifier), reverse("driver_app:home"))
                self.client.post(reverse("driver_app:logout"))

    def test_wrong_password_and_inactive_driver_are_refused(self):
        self.assertContains(self.sign_in("sam@example.com", "nope"), "isn&#x27;t right")
        self.driver.status = "suspended"
        self.driver.save()
        self.assertContains(self.sign_in("sam@example.com"), "isn&#x27;t right")

    def test_lockout_after_repeated_failures_even_with_the_right_password(self):
        for _ in range(MAX_FAILURES):
            self.sign_in("8175550155", "wrong")
        response = self.sign_in("(817) 555-0155")
        self.assertContains(response, "Too many attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_driver_login_cannot_open_the_admin(self):
        self.sign_in("sam@example.com")
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_staff_without_a_driver_record_are_sent_to_driver_sign_in(self):
        User.objects.create_user(username="clerk", password="testpass123", is_staff=True)
        self.client.login(username="clerk", password="testpass123")
        self.assertRedirects(self.client.get(reverse("driver_app:home")), self.url)

    def test_first_sign_in_requires_a_new_password(self):
        setup_driver_login(self.driver, actor=None)  # fresh temporary password
        self.driver.user.set_password("Temp-pass-1234")
        self.driver.user.save()
        self.sign_in("sam@example.com", "Temp-pass-1234")
        self.assertRedirects(self.client.get(reverse("driver_app:home")), reverse("driver_app:password"))
        response = self.client.post(
            reverse("driver_app:password"),
            {"old_password": "Temp-pass-1234", "new_password1": "Another-ride-99", "new_password2": "Another-ride-99"},
        )
        self.assertRedirects(response, reverse("driver_app:home"))
        self.assertFalse(must_change_password(self.driver.user))

    def test_idle_session_is_signed_out(self):
        self.sign_in("sam@example.com")
        self.assertEqual(self.client.get(reverse("driver_app:home")).status_code, 200)
        later = __import__("time").time() + views.IDLE_TIMEOUT_SECONDS + 5
        with mock.patch("driver_app.views.time.time", return_value=later):
            response = self.client.get(reverse("driver_app:home"))
        self.assertRedirects(response, self.url)
        self.assertNotIn("_auth_user_id", self.client.session)


class ManifestTests(TestCase):
    def setUp(self):
        self.driver = driver_with_login()
        self.vehicle = make_vehicle()
        self.client.post(reverse("driver_app:login"), {"identifier": "sam@example.com", "password": PASSWORD})
        self.passenger = make_passenger(legal_name="Morgan Lee")

    def trip(self, **overrides):
        defaults = {"driver": self.driver, "vehicle": self.vehicle, "scheduled_pickup_datetime": timezone.now()}
        defaults.update(overrides)
        trip = direct_trip(self.passenger, **defaults)
        trip.save()
        return trip

    def test_shows_only_my_trips_for_today(self):
        mine = self.trip()
        other_driver = make_driver(license_number="X2", phone="817-555-0111")
        theirs = self.trip(driver=other_driver)
        next_week = self.trip(scheduled_pickup_datetime=timezone.now() + timedelta(days=7))

        response = self.client.get(reverse("driver_app:home"))

        self.assertContains(response, str(mine.pk))
        self.assertNotContains(response, str(theirs.pk))
        self.assertNotContains(response, str(next_week.pk))
        for trip in (theirs, next_week):
            self.assertEqual(self.client.get(reverse("driver_app:trip", args=[trip.pk])).status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action="viewed", resource_type="DriverManifest").exists())

    def test_trip_page_has_directions_call_button_and_first_step(self):
        trip = self.trip()
        response = self.client.get(reverse("driver_app:trip", args=[trip.pk]))
        self.assertContains(response, "https://www.google.com/maps/dir/?api=1&amp;destination=1%20Main%20St")
        self.assertContains(response, "tel:817-555-0199")
        self.assertContains(response, "En route to pickup")
        self.assertTrue(
            AuditLog.objects.filter(action="viewed", resource_type="Trip", resource_id=str(trip.pk), source="driver_app").exists()
        )

    def step(self, trip, event_type, **extra):
        data = {"event_type": event_type, "latitude": "32.7555", "longitude": "-97.3308", "accuracy": "12"}
        data.update(extra)
        return self.client.post(reverse("driver_app:step", args=[trip.pk]), data)

    def test_full_ride_is_stamped_and_completes_the_trip(self):
        trip = self.trip()
        for event_type in ("en_route", "arrived_pickup", "picked_up"):
            self.step(trip, event_type)
            trip.refresh_from_db()
            self.assertEqual(trip.status, TripStatus.IN_PROGRESS)
        response = self.step(trip, "dropped_off")
        self.assertRedirects(response, reverse("driver_app:home"))
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.COMPLETED)
        events = list(trip.events.all())
        self.assertEqual(
            [e.event_type for e in events], ["en_route", "arrived_pickup", "picked_up", "dropped_off"]
        )
        self.assertTrue(all(e.has_location and e.recorded_by == self.driver.user for e in events))
        self.assertEqual(str(events[0].latitude), "32.755500")

    def test_steps_out_of_order_are_refused(self):
        trip = self.trip()
        self.step(trip, "dropped_off")
        self.assertFalse(trip.events.exists())

    def test_no_show_needs_a_reason_and_closes_the_trip(self):
        trip = self.trip()
        self.step(trip, "arrived_pickup")
        self.step(trip, "no_show")
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_PROGRESS)
        self.step(trip, "no_show", reason="not_present", note="Knocked twice")
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.NO_SHOW)
        self.assertEqual(trip.events.last().reason, "not_present")

    def test_step_without_location_is_saved_and_flagged(self):
        trip = self.trip()
        response = self.step(trip, "en_route", latitude="", longitude="", follow=False)
        self.assertEqual(response.status_code, 302)
        event = trip.events.get()
        self.assertFalse(event.has_location)
        self.assertIsNone(event.accuracy_m)


class EventRulesTests(TestCase):
    def test_bad_coordinates_are_dropped_not_stored(self):
        trip = direct_trip(make_passenger(), driver=make_driver(), vehicle=make_vehicle())
        trip.save()
        event = record_trip_event(trip, "en_route", user=None, latitude="999", longitude="-97")
        self.assertFalse(event.has_location)

    def test_trip_cancelled_by_dispatch_takes_no_more_steps(self):
        trip = direct_trip(make_passenger(), status=TripStatus.CANCELLED)
        trip.save()
        with self.assertRaises(TripEventError):
            record_trip_event(trip, "en_route", user=None)


class AdminAppLoginTests(TestCase):
    def setUp(self):
        self.driver = make_driver(email="sam@example.com")
        self.url = reverse("admin:drivers_driver_app_login", args=[self.driver.pk])

    def staff(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")

    def test_administrator_creates_login_and_sees_handover_once(self):
        self.staff("Administrator")
        self.assertContains(self.client.get(self.url), "Create app login")
        response = self.client.post(self.url)
        self.assertContains(response, "Handover sheet")
        self.assertIn("no-store", response["Cache-Control"])
        self.driver.refresh_from_db()
        self.assertIsNotNone(self.driver.user)
        change = self.client.get(reverse("admin:drivers_driver_change", args=[self.driver.pk]))
        self.assertContains(change, "Reset password")

    def test_dispatcher_cannot_create_logins(self):
        self.staff("Dispatcher")
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.driver.refresh_from_db()
        self.assertIsNone(self.driver.user)

    def test_trip_admin_shows_the_timeline(self):
        self.staff("Administrator")
        trip = direct_trip(make_passenger(), driver=self.driver, vehicle=make_vehicle())
        trip.save()
        record_trip_event(trip, "en_route", user=None, latitude="32.75", longitude="-97.33", accuracy="9")
        response = self.client.get(reverse("admin:trips_trip_change", args=[trip.pk]))
        self.assertContains(response, "Timeline (from the driver app)")
        self.assertContains(response, "maps?q=32.750000,-97.330000")
        self.assertEqual(TripEvent.objects.count(), 1)
        self.assertEqual(Trip.objects.get().status, TripStatus.IN_PROGRESS)

import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditLog
from transportation.models import TripRequest, TripRequestStatus
from trips.models import Trip, TripStatus
from trips.tests import make_driver, make_passenger, make_vehicle

from . import services
from .models import RiderAccount

PASSWORD = "Rides-to-dialysis-27"
BOOKING = {
    "full_name": "Jamie Chen",
    "phone": "817-555-0199",
    "email": "Jamie.Chen@example.com",
    "pickup_address": "77 Oak Lane, Arlington, TX",
    "dropoff_address": "9 Clinic Dr, Fort Worth, TX",
    "service_type": "wheelchair",
    "mobility_notes": "Power wheelchair",
}


def book(client, **overrides):
    data = {**BOOKING, "requested_datetime": (timezone.now() + timedelta(days=2)).isoformat(), **overrides}
    response = client.post("/api/v1/trip-requests/", data, format="json")
    assert response.status_code == 201, response.content
    return TripRequest.objects.get(pk=response.json()["id"])


def link_from(message) -> tuple[str, str]:
    match = re.search(r"\?uid=([\w-]+)&token=([\w-]+)", message.body)
    return match.group(1), match.group(2)


class RiderClient(APIClient):
    """Behaves like the website: fetches the CSRF cookie, sends it back."""

    def __init__(self, **kwargs):
        super().__init__(enforce_csrf_checks=True, **kwargs)

    def _token(self):
        if "csrftoken" not in self.cookies:
            self.get("/api/v1/rider/csrf/")
        return self.cookies["csrftoken"].value

    def write(self, method, path, data=None):
        return getattr(self, method)(path, data or {}, format="json", HTTP_X_CSRFTOKEN=self._token())


@override_settings(WEBSITE_URL="https://www.angelcaretransit.com")
class AccountOfferTests(TestCase):
    def setUp(self):
        cache.clear()  # DRF throttle counters
        self.client = RiderClient()

    def test_first_booking_creates_an_account_and_emails_a_phi_free_link(self):
        book(self.client)
        account = RiderAccount.objects.get()
        self.assertEqual(account.email, "jamie.chen@example.com")
        self.assertIsNone(account.verified_at)
        self.assertFalse(account.user.has_usable_password())
        self.assertFalse(account.user.is_staff)
        invite = next(m for m in mail.outbox if "set up your account" in m.subject)
        self.assertEqual(invite.to, ["jamie.chen@example.com"])
        self.assertIn("https://www.angelcaretransit.com/account/set-password?uid=", invite.body)
        for detail in ("Jamie", "Chen", "Oak Lane", "Clinic", "Wheelchair", "wheelchair"):
            self.assertNotIn(detail, invite.subject + invite.body + invite.alternatives[0][0])

    def test_later_bookings_attach_without_a_new_invite_and_facility_bookings_get_none(self):
        book(self.client)
        book(self.client, email="jamie.chen@EXAMPLE.com")
        self.assertEqual(RiderAccount.objects.count(), 1)
        self.assertEqual(sum("set up your account" in m.subject for m in mail.outbox), 1)
        book(self.client, email="planner@hospital.example", booked_for="facility")
        self.assertFalse(RiderAccount.objects.filter(email="planner@hospital.example").exists())

    def test_unused_invite_is_resent_after_a_day(self):
        book(self.client)
        RiderAccount.objects.update(invited_at=timezone.now() - timedelta(days=2))
        book(self.client)
        self.assertEqual(sum("set up your account" in m.subject for m in mail.outbox), 2)

    def test_account_trouble_never_fails_the_booking(self):
        from unittest import mock

        with mock.patch("riders.services.send_invite", side_effect=RuntimeError("smtp down")):
            book(self.client)
        self.assertEqual(TripRequest.objects.count(), 1)


class PasswordLinkTests(TestCase):
    def setUp(self):
        cache.clear()  # DRF throttle counters
        self.client = RiderClient()
        book(self.client)
        self.uid, self.token = link_from(mail.outbox[-1])

    def set_password(self, password=PASSWORD, token=None):
        return self.client.write(
            "post", "/api/v1/rider/set-password/", {"uid": self.uid, "token": token or self.token, "password": password}
        )

    def test_setting_the_password_verifies_and_signs_in(self):
        response = self.set_password()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["email"], "jamie.chen@example.com")
        self.assertIsNotNone(RiderAccount.objects.get().verified_at)
        self.assertEqual(self.client.get("/api/v1/rider/me/").status_code, 200)

    def test_link_works_once_and_bad_tokens_and_weak_passwords_fail(self):
        self.assertEqual(self.set_password("123").status_code, 400)
        self.assertEqual(self.set_password(token="nope-nope").status_code, 400)
        self.assertEqual(self.set_password().status_code, 200)
        self.assertEqual(self.set_password("Another-pass-2027").status_code, 400)

    def test_csrf_is_enforced_even_before_sign_in(self):
        bare = APIClient(enforce_csrf_checks=True)
        response = bare.post(
            "/api/v1/rider/set-password/", {"uid": self.uid, "token": self.token, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 403)


def verified_rider(client, email="jamie.chen@example.com"):
    book(client, email=email)
    uid, token = link_from(mail.outbox[-1])
    client.write("post", "/api/v1/rider/set-password/", {"uid": uid, "token": token, "password": PASSWORD})
    client.write("post", "/api/v1/rider/logout/")
    return RiderAccount.objects.get(email=email)


class SignInTests(TestCase):
    def setUp(self):
        cache.clear()  # DRF throttle counters
        self.client = RiderClient()

    def login(self, email="jamie.chen@example.com", password=PASSWORD):
        return self.client.write("post", "/api/v1/rider/login/", {"email": email, "password": password})

    def test_unverified_accounts_cannot_sign_in(self):
        book(self.client)
        RiderAccount.objects.get().user.set_password(PASSWORD)
        self.assertEqual(self.login().status_code, 400)

    def test_sign_in_sets_a_short_idle_session(self):
        verified_rider(self.client)
        self.assertEqual(self.login("JAMIE.CHEN@example.com").status_code, 200)
        self.assertEqual(self.client.session.get_expiry_age(), 15 * 60)
        self.assertEqual(self.client.get("/api/v1/rider/trips/").status_code, 200)

    def test_lockout_after_repeated_failures(self):
        verified_rider(self.client)
        for _ in range(5):
            self.login(password="wrong")
        self.assertEqual(self.login().status_code, 429)

    def test_staff_and_drivers_are_not_riders(self):
        User.objects.create_user(username="clerk", password="testpass123", is_staff=True)
        self.client.login(username="clerk", password="testpass123")
        self.assertEqual(self.client.get("/api/v1/rider/me/").status_code, 403)

    def test_forgot_password_is_silent_and_sends_the_right_email(self):
        verified_rider(self.client)
        before = len(mail.outbox)
        for email in ("nobody@example.com", "jamie.chen@example.com"):
            self.assertEqual(
                self.client.write("post", "/api/v1/rider/forgot-password/", {"email": email}).json(), {"ok": True}
            )
        self.assertEqual(len(mail.outbox), before + 1)
        self.assertIn("/account/reset-password?uid=", mail.outbox[-1].body)


class MyTripsTests(TestCase):
    def setUp(self):
        cache.clear()  # DRF throttle counters
        self.client = RiderClient()
        self.account = verified_rider(self.client)
        self.client.write("post", "/api/v1/rider/login/", {"email": self.account.email, "password": PASSWORD})
        self.request = TripRequest.objects.get(email__iexact=self.account.email)

    def trips(self):
        return self.client.get("/api/v1/rider/trips/").json()["trips"]

    def test_sees_only_own_non_facility_requests(self):
        other = RiderClient()
        book(other, email="someone@example.com")
        book(other, email=self.account.email, booked_for="facility")
        trips = self.trips()
        self.assertEqual([t["id"] for t in trips], [str(self.request.id)])
        self.assertEqual(trips[0]["status"], "received")
        self.assertTrue(trips[0]["can_cancel"])
        self.assertTrue(AuditLog.objects.filter(action="viewed_list", resource_type="RiderTrips").exists())

    def test_confirmed_trip_shows_time_driver_first_name_and_vehicle(self):
        trip = Trip.objects.create(
            trip_request=self.request.__class__.objects.get(pk=self.request.pk),
            passenger=make_passenger(),
            driver=make_driver(legal_name="Sam Taylor"),
            vehicle=make_vehicle(),
            scheduled_pickup_datetime=timezone.now() + timedelta(days=2, hours=1),
        )
        TripRequest.objects.filter(pk=self.request.pk).update(status=TripRequestStatus.SCHEDULED)
        item = self.trips()[0]
        self.assertEqual(item["status"], "confirmed")
        self.assertEqual(item["driver_first_name"], "Sam")
        self.assertEqual(item["vehicle"], "Ford Transit")
        self.assertEqual(item["confirmed_pickup"], trip.scheduled_pickup_datetime.isoformat())

    def test_cancel_cancels_request_and_trip_and_alerts_staff_without_phi(self):
        trip = Trip.objects.create(
            trip_request=self.request, passenger=make_passenger(),
            scheduled_pickup_datetime=timezone.now() + timedelta(days=2),
        )
        TripRequest.objects.filter(pk=self.request.pk).update(status=TripRequestStatus.SCHEDULED)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.write("post", f"/api/v1/rider/trips/{self.request.pk}/cancel/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "cancelled")
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.CANCELLED)
        alert = mail.outbox[-1]
        self.assertIn("cancelled by the rider", alert.subject)
        self.assertNotIn("Jamie", alert.body)
        self.assertFalse(self.trips()[0]["can_cancel"])

    def test_cannot_cancel_past_or_someone_elses_request(self):
        TripRequest.objects.filter(pk=self.request.pk).update(requested_datetime=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.client.write("post", f"/api/v1/rider/trips/{self.request.pk}/cancel/").status_code, 400)
        theirs = book(RiderClient(), email="someone@example.com")
        self.assertEqual(self.client.write("post", f"/api/v1/rider/trips/{theirs.pk}/cancel/").status_code, 404)

    def test_round_trip_shows_the_ride_home_and_cancels_both_legs(self):
        from trips.models import TripLeg
        from trips.returns import create_return_leg

        TripRequest.objects.filter(pk=self.request.pk).update(return_ride="will_call")
        outbound = Trip.objects.create(
            trip_request=TripRequest.objects.get(pk=self.request.pk),
            passenger=make_passenger(),
            leg=TripLeg.OUTBOUND,
            scheduled_pickup_datetime=timezone.now() + timedelta(days=2),
        )
        back = create_return_leg(outbound, actor=self.account.user)
        TripRequest.objects.filter(pk=self.request.pk).update(status=TripRequestStatus.SCHEDULED)
        self.assertEqual(
            self.trips()[0]["return_ride"],
            {"kind": "will_call", "text": "Yes, I'll call when I'm ready", "pickup": None},
        )
        self.assertEqual(self.client.write("post", f"/api/v1/rider/trips/{self.request.pk}/cancel/").status_code, 200)
        outbound.refresh_from_db()
        back.refresh_from_db()
        self.assertEqual((outbound.status, back.status), (TripStatus.CANCELLED, TripStatus.CANCELLED))

    def test_profile_and_saved_addresses(self):
        me = self.client.write("patch", "/api/v1/rider/me/", {"phone": "817-555-0100", "mobility_notes": "Walker"}).json()
        self.assertEqual((me["phone"], me["mobility_notes"]), ("817-555-0100", "Walker"))
        home = {"street": "77 Oak Lane", "city": "Arlington", "state": "TX", "zip": "76010"}
        me = self.client.write("post", "/api/v1/rider/addresses/", {"label": "Home", **home}).json()
        saved = me["addresses"][0]
        self.assertEqual((saved["label"], saved["address"], saved["zip"]), ("Home", "77 Oak Lane, Arlington, TX 76010", "76010"))
        bad = self.client.write("post", "/api/v1/rider/addresses/", {**home, "zip": "7"})
        self.assertEqual((bad.status_code, list(bad.json())), (400, ["zip"]))
        me = self.client.write("delete", f"/api/v1/rider/addresses/{saved['id']}/").json()
        self.assertEqual(me["addresses"], [])
        for i in range(10):
            self.client.write("post", "/api/v1/rider/addresses/", {**home, "street": f"{i} Main St"})
        self.assertEqual(self.client.write("post", "/api/v1/rider/addresses/", home).status_code, 400)

    def test_writes_need_the_csrf_token(self):
        response = self.client.patch("/api/v1/rider/me/", {"phone": "1"}, format="json")
        self.assertEqual(response.status_code, 403)


class StatusWordingTests(TestCase):
    def test_every_request_status_has_rider_wording(self):
        request = TripRequest(status=TripRequestStatus.NEW)
        for value in TripRequestStatus.values:
            request.status = value
            self.assertTrue(services.rider_status(request)[1])

"""Facility accounts, portal sign-in, bookings and follow-ups (ADR 0042)."""

from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.dashboard import build_dashboard
from accounts.otp_test_utils import login_with_otp
from riders.models import RiderAccount
from riders.tests import RiderClient, link_from
from transportation.models import BookedFor, TripRequest, TripRequestStatus
from trips.models import Trip, TripStatus
from trips.returns import create_return_leg
from trips.tests import make_passenger

from . import services
from .admin import import_facilities
from .models import Facility, FacilityContact, FacilityKind, FacilityUser, FollowUpDigest, Stage

PASSWORD = "Discharge-planning-27"
BOOKING = {
    "full_name": "Pat Rivera",
    "phone": "817-555-0142",
    "pickup_street": "1500 Hospital Dr",
    "pickup_city": "Fort Worth",
    "pickup_state": "TX",
    "pickup_zip": "76104",
    "dropoff_street": "22 Elm St",
    "dropoff_city": "Fort Worth",
    "dropoff_state": "TX",
    "dropoff_zip": "76110",
    "service_type": "wheelchair",
    "return_ride": "none",
}


class PortalClient(RiderClient):
    def _token(self):
        if "csrftoken" not in self.cookies:
            self.get("/api/v1/facility/csrf/")
        return self.cookies["csrftoken"].value


def code_from(message) -> str:
    return message.subject.split()[0]


@override_settings(WEBSITE_URL="https://www.angelcaretransit.com")
class PortalTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.facility = Facility.objects.create(name="Harris Methodist", kind=FacilityKind.HOSPITAL, stage=Stage.ACTIVE,
                                                street="1301 Pennsylvania Ave", city="Fort Worth", zip="76104")
        self.client = PortalClient()

    def portal_user(self, email="planner@harris.example.com", facility=None, *, verified=True):
        portal_user = services.create_portal_user(
            FacilityUser(facility=facility or self.facility, email=email, full_name="Robin Planner"), actor=None)
        if verified:
            portal_user.user.set_password(PASSWORD)
            portal_user.user.save()
            portal_user.verified_at = timezone.now()
            portal_user.save()
        return portal_user

    def sign_in(self, client, email="planner@harris.example.com"):
        mail.outbox.clear()
        response = client.write("post", "/api/v1/facility/login/", {"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.content
        code = code_from(mail.outbox[-1])
        response = client.write("post", "/api/v1/facility/login/code/", {"code": code})
        assert response.status_code == 200, response.content
        return response

    def book(self, client, **overrides):
        data = {**BOOKING, "requested_datetime": (timezone.now() + timedelta(days=2)).isoformat(), **overrides}
        response = client.write("post", "/api/v1/facility/book/", data)
        assert response.status_code == 201, response.content
        return TripRequest.objects.get(pk=response.json()["id"])


class AccountTests(PortalTestCase):
    def test_office_invite_then_set_password_signs_in(self):
        with self.captureOnCommitCallbacks(execute=True):
            portal_user = self.portal_user("Robin@Harris.example.com", verified=False)
        self.assertEqual(portal_user.email, "robin@harris.example.com")
        self.assertFalse(portal_user.user.has_usable_password())
        self.assertFalse(portal_user.user.is_staff)
        invite = mail.outbox[-1]
        self.assertIn("https://www.angelcaretransit.com/facility/set-password?uid=", invite.body)
        uid, token = link_from(invite)
        response = self.client.write("post", "/api/v1/facility/set-password/",
                                     {"uid": uid, "token": token, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["facility"]["name"], "Harris Methodist")
        self.assertEqual(self.client.get("/api/v1/facility/me/").status_code, 200)
        again = self.client.write("post", "/api/v1/facility/set-password/",
                                  {"uid": uid, "token": token, "password": "Another-pass-28"})
        self.assertEqual(again.status_code, 400)  # one use only

    def test_sign_in_needs_the_password_and_then_the_emailed_code(self):
        self.portal_user()
        response = self.client.write("post", "/api/v1/facility/login/",
                                     {"email": "planner@harris.example.com", "password": PASSWORD})
        self.assertEqual(response.json(), {"code_sent": True})
        self.assertEqual(self.client.get("/api/v1/facility/trips/").status_code, 403)  # not yet
        code = code_from(mail.outbox[-1])
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(self.client.write("post", "/api/v1/facility/login/code/", {"code": wrong}).status_code, 400)
        self.assertEqual(self.client.write("post", "/api/v1/facility/login/code/", {"code": code}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/facility/trips/").status_code, 200)
        self.assertEqual(self.client.session.get_expiry_age(), 15 * 60)

    def test_a_code_without_a_password_step_gets_nothing(self):
        self.assertEqual(self.client.write("post", "/api/v1/facility/login/code/", {"code": "123456"}).status_code, 400)

    def test_wrong_passwords_lock_out_and_unknown_emails_look_the_same(self):
        self.portal_user()
        for _ in range(5):
            response = self.client.write("post", "/api/v1/facility/login/",
                                         {"email": "planner@harris.example.com", "password": "wrong"})
            self.assertEqual(response.status_code, 400)
        locked = self.client.write("post", "/api/v1/facility/login/",
                                   {"email": "planner@harris.example.com", "password": PASSWORD})
        self.assertEqual(locked.status_code, 429)
        unknown = self.client.write("post", "/api/v1/facility/login/", {"email": "nobody@x.com", "password": "x"})
        self.assertEqual(unknown.json()["detail"], "That email or password isn't right.")

    def test_rider_and_staff_logins_are_not_portal_logins(self):
        staff = User.objects.create_user(username="staff1", password=PASSWORD, email="staff@x.com", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/api/v1/facility/me/").status_code, 403)

    def test_switched_off_users_are_shut_out(self):
        portal_user = self.portal_user()
        self.sign_in(self.client)
        portal_user.user.is_active = False
        portal_user.user.save()
        self.assertEqual(self.client.get("/api/v1/facility/trips/").status_code, 403)


class BookingTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        self.planner = self.portal_user()
        self.sign_in(self.client)

    def test_a_booking_is_a_request_for_the_office_with_no_rider_account(self):
        with self.captureOnCommitCallbacks(execute=True):
            request = self.book(self.client)
        self.assertEqual((request.facility, request.facility_user, request.booked_for, request.source),
                         (self.facility, self.planner, BookedFor.FACILITY, services.SOURCE))
        self.assertEqual(request.status, TripRequestStatus.NEW)
        self.assertEqual(request.email, self.planner.email)
        self.assertFalse(RiderAccount.objects.exists())
        staff_alert = next(m for m in mail.outbox if "New trip request" in m.subject)
        self.assertNotIn("Pat Rivera", staff_alert.body)

    def test_everyone_at_the_facility_sees_its_trips_and_nobody_else_does(self):
        request = self.book(self.client)
        colleague_client = PortalClient()
        self.portal_user("nurse@harris.example.com")
        self.sign_in(colleague_client, "nurse@harris.example.com")
        rows = colleague_client.get("/api/v1/facility/trips/").json()["trips"]
        self.assertEqual([(r["id"], r["rider_name"], r["booked_by"]) for r in rows],
                         [(str(request.pk), "Pat Rivera", "Robin Planner")])

        other = Facility.objects.create(name="Other Rehab", kind=FacilityKind.REHAB)
        outsider_client = PortalClient()
        self.portal_user("someone@other.example.com", facility=other)
        self.sign_in(outsider_client, "someone@other.example.com")
        self.assertEqual(outsider_client.get("/api/v1/facility/trips/").json()["trips"], [])
        cancel = outsider_client.write("post", f"/api/v1/facility/trips/{request.pk}/cancel/")
        self.assertEqual(cancel.status_code, 404)

    def test_scheduled_trip_keeps_the_facility_and_its_ride_back_is_not_listed_twice(self):
        request = self.book(self.client, return_ride="will_call")
        trip = Trip.objects.create(trip_request=request, passenger=make_passenger(),
                                   scheduled_pickup_datetime=request.requested_datetime)
        self.assertEqual(trip.facility, self.facility)
        back = create_return_leg(trip, actor=None)
        self.assertEqual(back.facility, self.facility)
        rows = self.client.get("/api/v1/facility/trips/").json()["trips"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["return_ride"]["kind"], "will_call")

    def test_office_booked_trips_for_the_facility_show_but_cant_be_cancelled_online(self):
        trip = Trip.objects.create(passenger=make_passenger(legal_name="Lee Office"), facility=self.facility,
                                   service_type="ambulatory", pickup_address="1 A St", dropoff_address="2 B St",
                                   scheduled_pickup_datetime=timezone.now() + timedelta(days=1))
        [row] = self.client.get("/api/v1/facility/trips/").json()["trips"]
        self.assertEqual((row["id"], row["rider_name"], row["status"], row["can_cancel"]),
                         (str(trip.pk), "Lee Office", "confirmed", False))

    def test_cancel_online_until_the_pickup(self):
        request = self.book(self.client)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.write("post", f"/api/v1/facility/trips/{request.pk}/cancel/")
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertTrue(any("cancelled by the facility" in m.subject for m in mail.outbox))


class FollowUpTests(PortalTestCase):
    def test_one_morning_email_listing_what_is_due(self):
        today = date(2027, 3, 1)
        Facility.objects.create(name="Due Today", next_follow_up=today, follow_up_note="Send rate sheet")
        Facility.objects.create(name="Overdue", next_follow_up=today - timedelta(days=3))
        Facility.objects.create(name="Lost One", next_follow_up=today, stage=Stage.LOST)
        Facility.objects.create(name="Later", next_follow_up=today + timedelta(days=1))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.send_follow_up_digest(today), 2)
        email = mail.outbox[-1]
        self.assertIn("2 facility follow-ups due", email.subject)
        self.assertIn("Due Today: Send rate sheet", email.body)
        self.assertIn("3 days overdue", email.body)
        self.assertNotIn("Lost One", email.body)
        self.assertEqual(services.send_follow_up_digest(today), 0)  # once a day
        self.assertEqual(FollowUpDigest.objects.count(), 1)

    def test_due_follow_ups_on_the_dashboard(self):
        Facility.objects.create(name="Call Back Clinic", next_follow_up=date(2020, 1, 1))
        admin = User.objects.create_user(username="admin1", password="x", is_staff=True)
        admin.groups.add(Group.objects.get(name="Administrator"))
        panel = next(p for p in build_dashboard(admin).panels if p.title == "Facility follow-ups due")
        self.assertEqual(panel.rows[0].cells[0], "Call Back Clinic")


class ImportTests(PortalTestCase):
    def test_csv_leads_with_contacts_and_duplicates_skipped(self):
        text = ("Facility Name,Type,Address,City,ZIP,Phone,Contact,Contact Title\n"
                "Sunrise Dialysis,Dialysis center,10 Main St,Arlington,76010,817-555-0111,Ana Cruz,Charge nurse\n"
                "Harris Methodist,Hospital,,Fort Worth,,,,\n"
                ",,,,,,,\n")
        made, skipped = import_facilities(text, user=None)
        self.assertEqual([f.name for f in made], ["Sunrise Dialysis"])
        sunrise = made[0]
        self.assertEqual((sunrise.kind, sunrise.stage, sunrise.address),
                         (FacilityKind.DIALYSIS, Stage.LEAD, "10 Main St, Arlington, TX 76010"))
        self.assertEqual(FacilityContact.objects.get(facility=sunrise).name, "Ana Cruz")
        self.assertIn("already on file", skipped[0])
        self.assertEqual(import_facilities("Phone\n1\n", user=None)[0], [])


class AdminTests(PortalTestCase):
    def login(self, group):
        self.client = Client()  # the admin, not the portal
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")
        return user

    def test_administrator_adds_a_portal_user_and_logs_a_call(self):
        admin_user = self.login("Administrator")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/admin/facilities/facilityuser/add/", {
                "facility": str(self.facility.pk), "full_name": "Dana Social", "email": "Dana@Harris.example.com",
                "title": "Social worker", "phone": "",
            }, follow=True)
        self.assertContains(response, "Set-up link emailed to dana@harris.example.com")
        self.assertIn("facility portal account", mail.outbox[-1].subject)

        page = self.client.get(f"/admin/facilities/facility/{self.facility.pk}/change/")
        self.assertContains(page, "Dana Social")
        upload = self.client.post("/admin/facilities/facility/import/", {
            "file": SimpleUploadedFile("leads.csv", b"name,city\nNew Lead,Dallas\n")})
        self.assertContains(upload, "1 added")
        self.assertEqual(self.client.get("/admin/facilities/facility/?follow_up=due").status_code, 200)
        self.assertEqual(admin_user.has_perm("facilities.add_facilityuser"), True)

    def test_clearing_follow_ups_in_bulk(self):
        self.login("Dispatcher")
        Facility.objects.update(next_follow_up=date(2026, 11, 6), follow_up_note="Call")
        response = self.client.post("/admin/facilities/facility/", {
            "action": "clear_follow_up", "_selected_action": [str(f.pk) for f in Facility.objects.all()]}, follow=True)
        self.assertContains(response, "Follow-up cleared on")
        self.assertFalse(Facility.objects.filter(next_follow_up__isnull=False).exists())

    def test_dispatchers_see_facilities_but_dont_hand_out_logins(self):
        self.login("Dispatcher")
        self.assertEqual(self.client.get("/admin/facilities/facility/").status_code, 200)
        self.assertEqual(self.client.get("/admin/facilities/facilityuser/add/").status_code, 403)

"""The trash bin (ADR 0046): deletes are kept whole, restored with their links, or purged after a check."""

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from billing.tests import make_invoice
from facilities.models import Facility, FacilityContact
from passengers.models import Passenger
from transportation.models import TripRequest
from trips.models import Trip, TripEvent
from trips.tests import make_passenger, make_trip_request

from . import services
from .models import DeletedFrom, TrashEntry


def staff(username, group):
    user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


class TrashTestCase(TestCase):
    def setUp(self):
        self.admin = staff("admin1", "Administrator")
        login_with_otp(self.client, username="admin1", password="testpass123")


class DeleteAndRestoreTests(TrashTestCase):
    def test_a_deleted_passenger_goes_to_the_trash_with_unbilled_trips_and_comes_back_linked(self):
        rider = make_passenger(legal_name="TEST Rider")
        request = make_trip_request(passenger=rider)
        trip = Trip.objects.create(passenger=rider, service_type="wheelchair", pickup_address="1 Main St",
                                   dropoff_address="2 Clinic Rd", scheduled_pickup_datetime=timezone.now())
        TripEvent.objects.create(trip=trip, event_type="en_route", signature=b"PNG-test")

        page = self.client.get(f"/admin/passengers/passenger/{rider.pk}/delete/")
        self.assertContains(page, "TEST Rider")
        self.client.post(f"/admin/passengers/passenger/{rider.pk}/delete/", {"post": "yes"})

        self.assertFalse(Passenger.objects.filter(pk=rider.pk).exists())
        self.assertFalse(Trip.objects.exists())  # gone from every list, the day board and reports
        self.assertIsNone(TripRequest.objects.get(pk=request.pk).passenger_id)
        entry = TrashEntry.objects.get()
        self.assertEqual((entry.label, entry.deleted_by, entry.deleted_from), ("TEST Rider", self.admin, "admin"))
        self.assertContains(self.client.get(f"/admin/trash/trashentry/{entry.pk}/change/"), "TEST Rider")

        self.client.post(f"/admin/trash/trashentry/{entry.pk}/restore/")
        trip.refresh_from_db()
        self.assertEqual(trip.passenger_id, rider.pk)
        self.assertEqual(trip.events.count(), 1)
        self.assertEqual(TripRequest.objects.get(pk=request.pk).passenger_id, rider.pk)
        self.assertFalse(TrashEntry.objects.exists())
        self.assertTrue(LogEntry.objects.filter(object_id=str(rider.pk), change_message__contains="Restored").exists())

    def test_billed_records_block_the_delete(self):
        invoice = make_invoice()
        rider = invoice.trip.passenger
        page = self.client.get(f"/admin/passengers/passenger/{rider.pk}/delete/")
        self.assertContains(page, "billed: set the record Inactive instead")
        with self.assertRaises(services.TrashError):
            services.send(rider, user=self.admin)
        self.assertTrue(Passenger.objects.filter(pk=rider.pk).exists())

    def test_a_facility_comes_back_with_its_contacts(self):
        facility = Facility.objects.create(name="TEST Dialysis", city="Fort Worth")
        FacilityContact.objects.create(facility=facility, name="Ana Cruz")
        self.client.post("/admin/facilities/facility/", {"action": "delete_selected", "post": "yes",
                                                         "_selected_action": [str(facility.pk)]})
        self.assertFalse(Facility.objects.exists())
        services.restore(TrashEntry.objects.get(), user=self.admin)
        self.assertEqual(Facility.objects.get().contacts.get().name, "Ana Cruz")

    def test_search_and_filters(self):
        services.send(make_passenger(legal_name="TEST One"), user=self.admin)
        services.send(Facility.objects.create(name="Real Clinic"), user=self.admin)
        page = self.client.get("/admin/trash/trashentry/?q=TEST")
        self.assertContains(page, "TEST One")
        self.assertNotContains(page, "Real Clinic")
        self.assertContains(self.client.get("/admin/trash/trashentry/?type=facilities.facility"), "Real Clinic")


class PurgeTests(TrashTestCase):
    def test_permanent_delete_needs_the_confirmation_and_stays_in_admin_history(self):
        entry = services.send(make_passenger(legal_name="TEST Junk"), user=self.admin)
        confirm = self.client.get(f"/admin/trash/trashentry/{entry.pk}/purge/")
        self.assertContains(confirm, "TEST Junk")
        self.assertTrue(TrashEntry.objects.exists())
        self.client.post(f"/admin/trash/trashentry/{entry.pk}/purge/", {"confirm": "yes"})
        self.assertFalse(TrashEntry.objects.exists())
        log = LogEntry.objects.get(action_flag=DELETION, change_message__contains="Permanently")
        self.assertEqual((log.user, log.object_repr), (self.admin, "TEST Junk"))

    def test_bulk_purge_asks_first(self):
        entries = [services.send(make_passenger(legal_name=f"TEST {n}", phone=f"817-555-010{n}"), user=self.admin)
                   for n in range(2)]
        ids = [str(e.pk) for e in entries]
        page = self.client.post("/admin/trash/trashentry/", {"action": "purge_selected", "_selected_action": ids})
        self.assertContains(page, "Yes, delete 2 for good")
        self.assertEqual(TrashEntry.objects.count(), 2)
        self.client.post("/admin/trash/trashentry/", {"action": "purge_selected", "_selected_action": ids,
                                                      "confirm": "yes"})
        self.assertFalse(TrashEntry.objects.exists())


class AccessTests(TrashTestCase):
    def test_dispatchers_never_see_the_trash(self):
        entry = services.send(make_passenger(), user=self.admin)
        self.client.logout()
        staff("disp", "Dispatcher")
        login_with_otp(self.client, username="disp", password="testpass123")
        self.assertEqual(self.client.get("/admin/trash/trashentry/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/trash/trashentry/{entry.pk}/change/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/trash/trashentry/{entry.pk}/restore/").status_code, 403)
        self.assertNotContains(self.client.get("/admin/"), "/admin/trash/")
        self.assertEqual(DeletedFrom.ADMIN, entry.deleted_from)

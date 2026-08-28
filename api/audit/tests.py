from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from transportation.models import TripRequest

from .models import AuditLog
from .services import record_change


def make_trip_request() -> TripRequest:
    return TripRequest.objects.create(
        full_name="Test Passenger",
        phone="817-555-0100",
        email="passenger@example.com",
        pickup_address="1 Main St",
        dropoff_address="2 Clinic Rd",
        requested_datetime="2027-01-01T10:00:00Z",
        service_type="ambulatory",
    )


class RecordChangeServiceTests(TestCase):
    def test_records_actor_action_and_diff(self):
        user = User.objects.create_user(username="staffer", password="x")
        trip_request = make_trip_request()

        log = record_change(
            actor=user,
            action="status_changed",
            resource_type="TripRequest",
            resource_id=trip_request.id,
            before={"status": "new"},
            after={"status": "reviewed"},
            source="admin",
        )

        self.assertEqual(log.actor, user)
        self.assertEqual(log.before, {"status": "new"})
        self.assertEqual(log.after, {"status": "reviewed"})

    def test_anonymous_actor_is_stored_as_system_null(self):
        trip_request = make_trip_request()

        log = record_change(
            actor=AnonymousUser(),
            action="status_changed",
            resource_type="TripRequest",
            resource_id=trip_request.id,
            before={"status": "new"},
            after={"status": "reviewed"},
            source="api",
        )

        self.assertIsNone(log.actor)

    def test_str_representation_handles_missing_actor(self):
        trip_request = make_trip_request()
        log = record_change(
            actor=None,
            action="status_changed",
            resource_type="TripRequest",
            resource_id=trip_request.id,
            source="system",
        )
        self.assertIn("system", str(log))


class AuditLogAdminAccessTests(TestCase):
    """AuditLog entries must not be editable/deletable through /admin/."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.log = record_change(
            actor=self.user,
            action="status_changed",
            resource_type="TripRequest",
            resource_id="some-id",
            before={"status": "new"},
            after={"status": "reviewed"},
            source="admin",
        )

    def test_cannot_delete_audit_log_via_admin(self):
        response = self.client.get(f"/admin/audit/auditlog/{self.log.id}/delete/")
        self.assertEqual(response.status_code, 403)

    def test_cannot_add_audit_log_via_admin(self):
        response = self.client.get("/admin/audit/auditlog/add/")
        self.assertEqual(response.status_code, 403)


class TripRequestStatusChangeAuditIntegrationTests(TestCase):
    """
    Proves the real admin workflow -- changing a trip request's status
    as a logged-in staff user -- actually creates an AuditLog entry,
    not just that the service function works in isolation.
    """

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="dispatcher1", password="testpass123", is_staff=True
        )
        self.admin_user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="dispatcher1", password="testpass123")
        self.trip_request = make_trip_request()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/transportation/triprequest/{self.trip_request.id}/change/"
        get_response = self.client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        post_data = {
            "full_name": self.trip_request.full_name,
            "phone": self.trip_request.phone,
            "email": self.trip_request.email,
            "pickup_address": self.trip_request.pickup_address,
            "dropoff_address": self.trip_request.dropoff_address,
            "requested_datetime_0": "2027-01-01",
            "requested_datetime_1": "10:00:00",
            "service_type": self.trip_request.service_type,
            "mobility_notes": "",
            "additional_notes": "",
            "status": "reviewed",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_id=str(self.trip_request.id))
        self.assertEqual(logs.count(), 1)
        log = logs.first()
        self.assertEqual(log.actor, self.admin_user)
        self.assertEqual(log.before, {"status": "new"})
        self.assertEqual(log.after, {"status": "reviewed"})

    def test_saving_without_status_change_does_not_create_audit_entry(self):
        change_url = f"/admin/transportation/triprequest/{self.trip_request.id}/change/"
        post_data = {
            "full_name": self.trip_request.full_name,
            "phone": self.trip_request.phone,
            "email": self.trip_request.email,
            "pickup_address": self.trip_request.pickup_address,
            "dropoff_address": self.trip_request.dropoff_address,
            "requested_datetime_0": "2027-01-01",
            "requested_datetime_1": "10:00:00",
            "service_type": self.trip_request.service_type,
            "mobility_notes": "",
            "additional_notes": "",
            "status": self.trip_request.status,
        }
        self.client.post(change_url, post_data)

        self.assertEqual(
            AuditLog.objects.filter(resource_id=str(self.trip_request.id)).count(), 0
        )

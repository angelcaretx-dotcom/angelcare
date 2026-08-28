from django.contrib.auth.models import Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog

from .models import Passenger, PassengerStatus


def make_passenger(**overrides) -> Passenger:
    defaults = {
        "legal_name": "Morgan Lee",
        "phone": "817-555-0199",
        "email": "morgan@example.com",
    }
    defaults.update(overrides)
    return Passenger.objects.create(**defaults)


class PassengerModelTests(TestCase):
    def test_default_status_is_active(self):
        passenger = make_passenger()
        self.assertEqual(passenger.status, PassengerStatus.ACTIVE)

    def test_str_uses_preferred_name_when_set(self):
        passenger = make_passenger(legal_name="Morgan Lee", preferred_name="Mo")
        self.assertEqual(str(passenger), "Mo")

    def test_str_falls_back_to_legal_name(self):
        passenger = make_passenger(legal_name="Morgan Lee")
        self.assertEqual(str(passenger), "Morgan Lee")

    def test_invalid_phone_is_rejected(self):
        passenger = Passenger(legal_name="Bad Phone", phone="not-a-phone")
        with self.assertRaises(Exception):
            passenger.full_clean()


class PassengerAdminAccessTests(TestCase):
    def setUp(self):
        self.passenger = make_passenger()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_and_add_passengers(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher1", password="testpass123")

        list_response = self.client.get("/admin/passengers/passenger/")
        self.assertEqual(list_response.status_code, 200)

        add_response = self.client.get("/admin/passengers/passenger/add/")
        self.assertEqual(add_response.status_code, 200)

    def test_dispatcher_cannot_delete_passengers(self):
        self.make_staff_user(username="dispatcher2", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher2", password="testpass123")

        response = self.client.get(f"/admin/passengers/passenger/{self.passenger.id}/delete/")
        self.assertEqual(response.status_code, 403)

    def test_administrator_can_delete_passengers(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        login_with_otp(self.client, username="admin1", password="testpass123")

        response = self.client.post(
            f"/admin/passengers/passenger/{self.passenger.id}/delete/",
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Passenger.objects.filter(id=self.passenger.id).exists())


class PassengerStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dispatcher1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="dispatcher1", password="testpass123")
        self.passenger = make_passenger()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/passengers/passenger/{self.passenger.id}/change/"
        post_data = {
            "legal_name": self.passenger.legal_name,
            "preferred_name": "",
            "phone": self.passenger.phone,
            "email": self.passenger.email,
            "emergency_contact_name": "",
            "emergency_contact_phone": "",
            "preferred_service_type": "",
            "mobility_notes": "",
            "notes": "",
            "status": "inactive",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Passenger", resource_id=str(self.passenger.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "active"})
        self.assertEqual(logs.first().after, {"status": "inactive"})

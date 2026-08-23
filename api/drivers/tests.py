from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase

from audit.models import AuditLog

from .models import Driver, DriverStatus


def make_driver(**overrides) -> Driver:
    defaults = {
        "legal_name": "Sam Taylor",
        "phone": "817-555-0155",
        "employment_type": "employee",
        "license_number": "TX-DL-123456",
        "license_expiration_date": date.today() + timedelta(days=365),
    }
    defaults.update(overrides)
    return Driver.objects.create(**defaults)


class DriverModelTests(TestCase):
    def test_default_status_is_active(self):
        driver = make_driver()
        self.assertEqual(driver.status, DriverStatus.ACTIVE)

    def test_str_representation(self):
        driver = make_driver(legal_name="Sam Taylor")
        self.assertIn("Sam Taylor", str(driver))


class DriverAdminAccessTests(TestCase):
    def setUp(self):
        self.driver = make_driver()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_but_not_change_drivers(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        self.client.login(username="dispatcher1", password="testpass123")

        list_response = self.client.get("/admin/drivers/driver/")
        self.assertEqual(list_response.status_code, 200)

        # View-only permission renders the change page read-only (200),
        # not a 403 -- Django only blocks the actual save attempt.
        change_url = f"/admin/drivers/driver/{self.driver.id}/change/"
        get_response = self.client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(change_url, {"legal_name": "Changed Name"})
        self.assertEqual(post_response.status_code, 403)
        self.driver.refresh_from_db()
        self.assertNotEqual(self.driver.legal_name, "Changed Name")

    def test_administrator_can_change_drivers(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        self.client.login(username="admin1", password="testpass123")

        response = self.client.get(f"/admin/drivers/driver/{self.driver.id}/change/")
        self.assertEqual(response.status_code, 200)


class DriverStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        self.client.login(username="admin1", password="testpass123")
        self.driver = make_driver()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/drivers/driver/{self.driver.id}/change/"
        post_data = {
            "legal_name": self.driver.legal_name,
            "phone": self.driver.phone,
            "email": "",
            "employment_type": self.driver.employment_type,
            "license_number": self.driver.license_number,
            "license_expiration_date": self.driver.license_expiration_date.isoformat(),
            "notes": "",
            "status": "suspended",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Driver", resource_id=str(self.driver.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "active"})
        self.assertEqual(logs.first().after, {"status": "suspended"})

from django.contrib.auth.models import Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog

from .models import Payer, PayerStatus


def make_payer(**overrides) -> Payer:
    defaults = {
        "name": "Sample Medicaid MCO",
        "payer_type": "medicaid_mco",
        "contact_name": "Jordan Rivera",
        "phone": "817-555-0177",
        "email": "jordan@example.com",
    }
    defaults.update(overrides)
    return Payer.objects.create(**defaults)


class PayerModelTests(TestCase):
    def test_default_status_is_active(self):
        payer = make_payer()
        self.assertEqual(payer.status, PayerStatus.ACTIVE)

    def test_str_representation(self):
        payer = make_payer(name="Sample Medicaid MCO")
        self.assertEqual(str(payer), "Sample Medicaid MCO")

    def test_invalid_phone_is_rejected(self):
        payer = Payer(name="Bad Phone Payer", payer_type="broker", phone="not-a-phone")
        with self.assertRaises(Exception):
            payer.full_clean()

    def test_phone_is_optional(self):
        payer = Payer(name="No Phone Payer", payer_type="private_pay")
        payer.full_clean()  # should not raise


class PayerAdminAccessTests(TestCase):
    def setUp(self):
        self.payer = make_payer()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_but_not_change_payers(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher1", password="testpass123")

        list_response = self.client.get("/admin/payers/payer/")
        self.assertEqual(list_response.status_code, 200)

        # View-only permission renders the change page read-only (200),
        # not a 403 -- Django only blocks the actual save attempt.
        change_url = f"/admin/payers/payer/{self.payer.id}/change/"
        get_response = self.client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(change_url, {"name": "Changed Name"})
        self.assertEqual(post_response.status_code, 403)
        self.payer.refresh_from_db()
        self.assertNotEqual(self.payer.name, "Changed Name")

    def test_administrator_can_change_payers(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        login_with_otp(self.client, username="admin1", password="testpass123")

        response = self.client.get(f"/admin/payers/payer/{self.payer.id}/change/")
        self.assertEqual(response.status_code, 200)


class PayerStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.payer = make_payer()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/payers/payer/{self.payer.id}/change/"
        post_data = {
            "name": self.payer.name,
            "payer_type": self.payer.payer_type,
            "contact_name": self.payer.contact_name,
            "phone": self.payer.phone,
            "email": self.payer.email,
            "notes": "",
            "status": "inactive",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Payer", resource_id=str(self.payer.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "active"})
        self.assertEqual(logs.first().after, {"status": "inactive"})

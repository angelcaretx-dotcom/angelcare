from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog

from .models import ComplianceRecord, ComplianceRecordStatus


def make_compliance_record(**overrides) -> ComplianceRecord:
    defaults = {
        "name": "General Liability Insurance",
        "record_type": "insurance_policy",
        "issuing_authority": "Sample Insurance Co",
        "expiration_date": date.today() + timedelta(days=365),
    }
    defaults.update(overrides)
    return ComplianceRecord.objects.create(**defaults)


class ComplianceRecordModelTests(TestCase):
    def test_default_status_is_active(self):
        record = make_compliance_record()
        self.assertEqual(record.status, ComplianceRecordStatus.ACTIVE)

    def test_str_includes_name_and_status(self):
        record = make_compliance_record(name="State Operating Permit")
        self.assertEqual(str(record), "State Operating Permit (active)")


class ComplianceRecordAdminAccessTests(TestCase):
    def setUp(self):
        self.record = make_compliance_record()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_but_not_change_compliance_records(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher1", password="testpass123")

        list_response = self.client.get("/admin/compliance/compliancerecord/")
        self.assertEqual(list_response.status_code, 200)

        # View-only permission renders the change page read-only (200),
        # not a 403 -- Django only blocks the actual save attempt.
        change_url = f"/admin/compliance/compliancerecord/{self.record.id}/change/"
        get_response = self.client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(change_url, {"name": "Changed Name"})
        self.assertEqual(post_response.status_code, 403)
        self.record.refresh_from_db()
        self.assertNotEqual(self.record.name, "Changed Name")

    def test_administrator_can_change_compliance_records(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        login_with_otp(self.client, username="admin1", password="testpass123")

        response = self.client.get(f"/admin/compliance/compliancerecord/{self.record.id}/change/")
        self.assertEqual(response.status_code, 200)


class ComplianceRecordStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.record = make_compliance_record()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/compliance/compliancerecord/{self.record.id}/change/"
        post_data = {
            "name": self.record.name,
            "record_type": self.record.record_type,
            "issuing_authority": self.record.issuing_authority,
            "reference_number": "",
            "issued_date": "",
            "expiration_date": self.record.expiration_date.isoformat(),
            "notes": "",
            "status": "expired",
            "documents-document-content_type-object_id-TOTAL_FORMS": "0",
            "documents-document-content_type-object_id-INITIAL_FORMS": "0",
            "documents-document-content_type-object_id-MIN_NUM_FORMS": "0",
            "documents-document-content_type-object_id-MAX_NUM_FORMS": "1000",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(
            resource_type="ComplianceRecord", resource_id=str(self.record.id)
        )
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "active"})
        self.assertEqual(logs.first().after, {"status": "expired"})

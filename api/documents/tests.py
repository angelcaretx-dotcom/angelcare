import shutil
import tempfile
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from audit.models import AuditLog
from drivers.models import Driver

from .models import Document, DocumentStatus, DocumentType

TEMP_MEDIA_ROOT = tempfile.mkdtemp()


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


def make_upload(name="license.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"fake pdf bytes", content_type="application/pdf")


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class DocumentModelTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_document_can_attach_to_a_driver(self):
        driver = make_driver()
        document = Document.objects.create(
            content_object=driver,
            document_type=DocumentType.DRIVER_LICENSE,
            file=make_upload(),
        )
        self.assertEqual(document.content_object, driver)
        self.assertEqual(
            document.content_type, ContentType.objects.get_for_model(Driver)
        )

    def test_default_status_is_pending(self):
        driver = make_driver()
        document = Document.objects.create(
            content_object=driver,
            document_type=DocumentType.DRIVER_LICENSE,
            file=make_upload(),
        )
        self.assertEqual(document.status, DocumentStatus.PENDING)

    def test_rejecting_without_reason_is_invalid(self):
        driver = make_driver()
        document = Document(
            content_object=driver,
            document_type=DocumentType.DRIVER_LICENSE,
            file=make_upload(),
            status=DocumentStatus.REJECTED,
        )
        with self.assertRaises(ValidationError) as ctx:
            document.full_clean()
        self.assertIn("rejection_reason", ctx.exception.message_dict)

    def test_rejecting_with_reason_is_valid(self):
        driver = make_driver()
        document = Document(
            content_object=driver,
            document_type=DocumentType.DRIVER_LICENSE,
            file=make_upload(),
            status=DocumentStatus.REJECTED,
            rejection_reason="Photo is blurry, please re-upload.",
        )
        document.full_clean()  # should not raise


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class DocumentAdminIntegrationTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.admin_user.groups.add(Group.objects.get(name="Administrator"))
        self.client.login(username="admin1", password="testpass123")
        self.driver = make_driver()

    def test_uploading_document_via_driver_inline_sets_uploaded_by(self):
        change_url = f"/admin/drivers/driver/{self.driver.id}/change/"
        post_data = {
            "legal_name": self.driver.legal_name,
            "phone": self.driver.phone,
            "email": "",
            "employment_type": self.driver.employment_type,
            "license_number": self.driver.license_number,
            "license_expiration_date": self.driver.license_expiration_date.isoformat(),
            "notes": "",
            "status": self.driver.status,
            "documents-document-content_type-object_id-TOTAL_FORMS": "1",
            "documents-document-content_type-object_id-INITIAL_FORMS": "0",
            "documents-document-content_type-object_id-MIN_NUM_FORMS": "0",
            "documents-document-content_type-object_id-MAX_NUM_FORMS": "1000",
            "documents-document-content_type-object_id-0-document_type": "driver_license",
            "documents-document-content_type-object_id-0-file": make_upload(),
            "documents-document-content_type-object_id-0-expiration_date": (
                self.driver.license_expiration_date.isoformat()
            ),
            "documents-document-content_type-object_id-0-notes": "",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        document = Document.objects.get(object_id=self.driver.id)
        self.assertEqual(document.uploaded_by, self.admin_user)
        self.assertEqual(document.status, DocumentStatus.PENDING)

    def test_verifying_a_document_sets_verified_by_and_logs_audit(self):
        document = Document.objects.create(
            content_object=self.driver,
            document_type=DocumentType.DRIVER_LICENSE,
            file=make_upload(),
        )
        response = self.client.post(
            f"/admin/documents/document/{document.id}/change/",
            {
                "document_type": "driver_license",
                "expiration_date": self.driver.license_expiration_date.isoformat(),
                "status": "verified",
                "rejection_reason": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        document.refresh_from_db()
        self.assertEqual(document.status, DocumentStatus.VERIFIED)
        self.assertEqual(document.verified_by, self.admin_user)
        self.assertIsNotNone(document.verified_at)

        logs = AuditLog.objects.filter(resource_type="Document", resource_id=str(document.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().after, {"status": "verified"})

    def test_dispatcher_can_view_but_not_change_documents(self):
        dispatcher = User.objects.create_user(
            username="dispatcher1", password="testpass123", is_staff=True
        )
        dispatcher.groups.add(Group.objects.get(name="Dispatcher"))
        self.client.logout()
        self.client.login(username="dispatcher1", password="testpass123")

        document = Document.objects.create(
            content_object=self.driver,
            document_type=DocumentType.DRIVER_LICENSE,
            file=make_upload(),
        )

        list_response = self.client.get("/admin/documents/document/")
        self.assertEqual(list_response.status_code, 200)

        post_response = self.client.post(
            f"/admin/documents/document/{document.id}/change/",
            {"status": "verified"},
        )
        self.assertEqual(post_response.status_code, 403)

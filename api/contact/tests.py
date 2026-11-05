from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from verification.services import issue_token

from .models import ContactMessage, MessageStatus

URL = "/api/v1/contact/"


def message(**overrides):
    data = {
        "name": "Pat Rivera",
        "email": "Pat@Example.com",
        "phone": "817-555-0100",
        "message": "Do you go to Baylor Scott & White in Dallas? My mother has dialysis.",
        **overrides,
    }
    data.setdefault("verification_token", issue_token(data["email"]))
    return data


@override_settings(STAFF_NOTIFICATION_EMAIL="staff@example.com", SITE_URL="https://admin.example.com")
class ContactFormTests(TestCase):
    def test_saved_and_staff_alerted_without_the_message_text(self):
        response = APIClient().post(URL, message(), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        saved = ContactMessage.objects.get()
        self.assertEqual((saved.email, saved.status), ("pat@example.com", MessageStatus.NEW))
        self.assertEqual(len(mail.outbox), 1)
        alert = mail.outbox[0]
        self.assertEqual(alert.to, ["staff@example.com"])
        self.assertIn(saved.reference, alert.subject)
        for private in ("Pat", "dialysis", "817-555-0100", "pat@example.com"):
            self.assertNotIn(private, alert.subject + alert.body)
        self.assertIn(f"/admin/contact/contactmessage/{saved.id}/change/", alert.body)

    def test_needs_a_token_for_the_same_email(self):
        for token in ("", issue_token("someone@example.com")):
            response = APIClient().post(URL, message(verification_token=token), format="json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("verification_token", response.json())
        self.assertFalse(ContactMessage.objects.exists())

    def test_blank_message_refused_and_trap_ignored(self):
        self.assertEqual(APIClient().post(URL, message(message="  "), format="json").status_code, 400)
        response = APIClient().post(URL, message(company_website="x"), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertFalse(ContactMessage.objects.exists())
        self.assertEqual(len(mail.outbox), 0)


class ContactAdminTests(TestCase):
    def test_dispatcher_reads_and_marks_handled(self):
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        saved = ContactMessage.objects.create(name="Pat", email="pat@example.com", message="Hello")
        url = f"/admin/contact/contactmessage/{saved.id}/change/"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="viewed", resource_id=str(saved.id)).exists())
        response = self.client.post(url, {"status": "handled"})
        self.assertEqual(response.status_code, 302)
        saved.refresh_from_db()
        self.assertEqual((saved.status, saved.handled_by), (MessageStatus.HANDLED, user))
        self.assertEqual(self.client.get("/admin/contact/contactmessage/add/").status_code, 403)

    def test_message_number_from_the_alert_is_shown_and_searchable(self):
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        wanted = ContactMessage.objects.create(name="Pat", email="pat@example.com", message="Hello")
        ContactMessage.objects.create(name="Lee", email="lee@example.com", message="Hi")
        listing = self.client.get("/admin/contact/contactmessage/").content.decode()
        self.assertIn(wanted.reference, listing)
        self.assertIn(wanted.reference, self.client.get(f"/admin/contact/contactmessage/{wanted.id}/change/").content.decode())
        for typed in (wanted.reference, wanted.reference.lstrip("#").lower()):
            found = self.client.get("/admin/contact/contactmessage/", {"q": typed}).content.decode()
            self.assertIn("pat@example.com", found)
            self.assertNotIn("lee@example.com", found)

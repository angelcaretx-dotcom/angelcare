import re

from django.contrib.auth.models import User
from django.test import TestCase

from audit.models import AuditLog

from . import mfa, onboarding
from .models import StaffOnboarding
from .otp_test_utils import login_with_otp
from .test_mfa import next_totp_code, real_login, staff
from .tests import current_totp_code


class CreateStaffMemberTests(TestCase):
    def setUp(self):
        self.root = staff("root", group=None, superuser=True)

    def create(self, **overrides):
        fields = {
            "email": "Pat.Lee@Example.com",
            "first_name": "Pat",
            "last_name": "Lee",
            "role": "Dispatcher",
            "actor": self.root,
        }
        fields.update(overrides)
        return onboarding.create_staff_member(**fields)

    def test_creates_a_ready_to_hand_over_account(self):
        user, password, codes = self.create()

        self.assertEqual((user.username, user.email), ("pat.lee@example.com", "pat.lee@example.com"))
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["Dispatcher"])
        self.assertTrue(user.check_password(password))
        self.assertRegex(password, r"^[a-hj-km-np-z2-9]{4}(-[a-hj-km-np-z2-9]{4}){3}$")
        self.assertEqual(len(codes), mfa.BACKUP_CODE_COUNT)
        self.assertEqual(mfa.backup_codes_remaining(user), mfa.BACKUP_CODE_COUNT)
        self.assertFalse(mfa.authenticators(user).exists())
        self.assertTrue(StaffOnboarding.objects.get(user=user).must_change_password)

        added = AuditLog.objects.get(action="staff_member_added")
        self.assertEqual((added.actor, added.after["role"]), (self.root, "Dispatcher"))
        self.assertNotIn(password, str(added.after))

    def test_dispatchers_cannot_add_staff_but_administrators_can(self):
        """ADR 0030: administrators manage staff, not only owners."""
        dispatcher = staff("disp1", group="Dispatcher")
        with self.assertRaises(onboarding.OnboardingError):
            self.create(actor=dispatcher)
        self.assertFalse(User.objects.filter(username="pat.lee@example.com").exists())
        self.create(actor=staff("admin1", group="Administrator"))
        self.assertTrue(User.objects.filter(username="pat.lee@example.com").exists())

    def test_refuses_duplicate_email_regardless_of_case(self):
        self.create()
        with self.assertRaisesMessage(onboarding.OnboardingError, "already an account"):
            self.create(email="PAT.LEE@example.com")

    def test_refuses_unknown_roles(self):
        with self.assertRaises(onboarding.OnboardingError):
            self.create(role="Superuser")

    def test_temporary_passwords_are_random(self):
        self.assertEqual(
            len({onboarding.generate_temporary_password() for _ in range(50)}), 50
        )


class AddStaffPageTests(TestCase):
    def test_dispatchers_cannot_reach_the_page(self):
        staff("disp1", group="Dispatcher")
        login_with_otp(self.client, username="disp1", password="testpass123")
        self.assertEqual(self.client.get("/admin/security/staff/add/").status_code, 403)

    def test_duplicate_email_shows_an_error_not_a_sheet(self):
        staff("root", group=None, superuser=True)
        login_with_otp(self.client, username="root", password="testpass123")
        data = {"email": "root@example.com", "first_name": "A", "last_name": "B", "role": "Dispatcher"}
        User.objects.create_user(username="root@example.com", email="root@example.com")
        response = self.client.post("/admin/security/staff/add/", data)
        self.assertContains(response, "already an account")
        self.assertNotContains(response, 'data-security="handover"')


class FirstLoginJourneyTests(TestCase):
    """From 'Add staff member' to a new dispatcher's first working screen."""

    def test_new_dispatcher_end_to_end(self):
        # A superuser adds them and gets the one-time handover sheet.
        staff("root", group=None, superuser=True)
        login_with_otp(self.client, username="root", password="testpass123")
        sheet = self.client.post(
            "/admin/security/staff/add/",
            {"email": "pat@example.com", "first_name": "Pat", "last_name": "Lee", "role": "Dispatcher"},
        )
        self.assertContains(sheet, 'data-security="handover"')
        self.assertIn("no-store", sheet["Cache-Control"])
        html = sheet.content.decode()
        password = re.search(r"([a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4})", html).group(1)
        codes = [c for c in StaticCodes.of("pat@example.com") if c in html]
        self.assertEqual(len(codes), mfa.BACKUP_CODE_COUNT)
        self.client.logout()

        # First login with the sheet's password and a code...
        response = self.client.post(
            "/admin/login/?next=/admin/",
            {"username": "pat@example.com", "password": password, "otp_token": codes[0]},
        )
        self.assertEqual(response.status_code, 302)

        # ...lands on "change your password", and nothing else is reachable.
        for path in ["/admin/", "/admin/transportation/triprequest/", "/admin/security/"]:
            self.assertRedirects(self.client.get(path), "/admin/password_change/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/admin/password_change/").status_code, 200)

        response = self.client.post(
            "/admin/password_change/",
            {"old_password": password, "new_password1": "Pat-chose-this-1!", "new_password2": "Pat-chose-this-1!"},
        )
        self.assertEqual(response.status_code, 302)
        pat = User.objects.get(username="pat@example.com")
        self.assertFalse(StaffOnboarding.objects.get(user=pat).must_change_password)
        self.assertTrue(AuditLog.objects.filter(action="staff_temporary_password_replaced", actor=pat).exists())

        # Next: an authenticator of their own is required.
        self.assertRedirects(self.client.get("/admin/"), "/admin/security/authenticators/add/", fetch_redirect_response=False)
        self.client.get("/admin/security/authenticators/add/")
        pending = mfa.pending_authenticator(pat)
        self.client.post("/admin/security/authenticators/add/", {"name": "Pat's phone", "code": current_totp_code(pending)})

        # Fully set up: the dashboard works, scoped to the Dispatcher role.
        home = self.client.get("/admin/")
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, 'data-dashboard="finance"')

        # And the next login uses their own password and phone.
        self.client.logout()
        device = mfa.authenticators(pat).get()
        self.assertTrue(real_login_as(self.client, "pat@example.com", "Pat-chose-this-1!", next_totp_code(device)))

    def test_logout_is_never_blocked(self):
        user = staff("sam", totp=False)
        StaffOnboarding.objects.create(user=user)
        code = mfa.generate_backup_codes(user, actor=user)[0]
        self.assertTrue(real_login(self.client, "sam", code))
        response = self.client.post("/admin/logout/")
        self.assertNotEqual(response.headers.get("Location"), "/admin/password_change/")
        self.assertEqual(self.client.get("/admin/").status_code, 302)  # logged out -> login page

    def test_existing_fully_set_up_staff_are_unaffected(self):
        staff("sam")
        login_with_otp(self.client, username="sam", password="testpass123")
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertFalse(onboarding.must_change_password(User.objects.get(username="sam")))

    def test_public_api_is_never_gated(self):
        user = staff("sam", totp=False)
        StaffOnboarding.objects.create(user=user)
        self.assertEqual(self.client.get("/healthz/").status_code, 200)


class StaticCodes:
    @staticmethod
    def of(username):
        from django_otp.plugins.otp_static.models import StaticToken

        return list(StaticToken.objects.filter(device__user__username=username).values_list("token", flat=True))


def real_login_as(client, username, password, code):
    response = client.post(
        "/admin/login/?next=/admin/",
        {"username": username, "password": password, "otp_token": code},
    )
    return response.status_code == 302 and client.get("/admin/").status_code == 200

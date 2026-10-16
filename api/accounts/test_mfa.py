from django.contrib.auth.models import Group, User
from django.test import TestCase
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.models import AuditLog

from . import mfa
from .otp_test_utils import login_with_otp
from .tests import current_totp_code


def staff(username, *, group="Dispatcher", superuser=False, totp=True):
    user = User.objects.create_user(
        username=username, password="testpass123", is_staff=True, is_superuser=superuser
    )
    if group:
        user.groups.add(Group.objects.get(name=group))
    if totp:
        TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
    return user


def next_totp_code(device) -> str:
    """The code the authenticator app will show in the next 30-second window."""
    import time

    from django_otp.oath import TOTP

    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + device.step
    return f"{totp.token():0{device.digits}d}"


def real_login(client, username, code):
    """Submit the actual /admin/login/ form, as a person would."""
    response = client.post(
        "/admin/login/?next=/admin/",
        {"username": username, "password": "testpass123", "otp_token": code},
    )
    return response.status_code == 302 and client.get("/admin/").status_code == 200


class BackupCodeServiceTests(TestCase):
    def test_generates_unique_single_format_codes_and_replaces_old_ones(self):
        user = staff("sam")
        first = mfa.generate_backup_codes(user, actor=user)
        self.assertEqual(len(first), mfa.BACKUP_CODE_COUNT)
        self.assertEqual(len(set(first)), mfa.BACKUP_CODE_COUNT)
        for code in first:
            self.assertRegex(code, r"^[a-z2-7]{8}$")  # can never look like a 6-digit TOTP code

        second = mfa.generate_backup_codes(user, actor=user)
        self.assertEqual(mfa.backup_codes_remaining(user), mfa.BACKUP_CODE_COUNT)
        self.assertFalse(StaticToken.objects.filter(token__in=first).exists())
        self.assertEqual(StaticDevice.objects.filter(user=user).count(), 1)
        self.assertNotEqual(set(first), set(second))

    def test_is_audited_without_ever_storing_the_codes(self):
        user = staff("sam")
        codes = mfa.generate_backup_codes(user, actor=user)
        entry = AuditLog.objects.get(action="mfa_backup_codes_generated")
        self.assertEqual(entry.actor, user)
        for code in codes:
            self.assertNotIn(code, str(entry.after))

    def test_only_superusers_issue_codes_for_someone_else(self):
        target = staff("locked_out")
        admin = staff("admin1", group="Administrator")
        with self.assertRaises(mfa.MfaError):
            mfa.generate_backup_codes(target, actor=admin)
        root = staff("root", group=None, superuser=True)
        mfa.generate_backup_codes(target, actor=root)
        entry = AuditLog.objects.get(action="mfa_recovery_codes_issued")
        self.assertEqual((entry.actor, entry.resource_id), (root, str(target.pk)))

    def test_fresh_codes_clear_a_lockout(self):
        user = staff("sam")
        mfa.generate_backup_codes(user, actor=user)
        StaticDevice.objects.filter(user=user).update(throttling_failure_count=9)
        mfa.generate_backup_codes(user, actor=user)
        self.assertEqual(StaticDevice.objects.get(user=user).throttling_failure_count, 0)

    def test_normalizes_codes_as_people_retype_them(self):
        self.assertEqual(mfa.normalize_backup_code(" ABCD-EFGH "), "abcdefgh")
        self.assertEqual(mfa.normalize_backup_code("abcd efgh"), "abcdefgh")


class BackupCodeLoginTests(TestCase):
    """Through the real login form -- not login_with_otp (see RealLoginFormTests)."""

    def test_a_backup_code_logs_in_once_however_it_is_typed(self):
        user = staff("sam")
        code = mfa.generate_backup_codes(user, actor=user)[0]
        typed = f"{code[:4].upper()}-{code[4:].upper()}"

        self.assertTrue(real_login(self.client, "sam", typed))
        self.assertEqual(mfa.backup_codes_remaining(user), mfa.BACKUP_CODE_COUNT - 1)

        self.client.logout()
        self.assertFalse(real_login(self.client, "sam", code))  # single use

    def test_backup_code_works_with_no_authenticator_at_all(self):
        # The lost-phone case: the TOTP device is gone entirely.
        user = staff("sam", totp=False)
        code = mfa.generate_backup_codes(user, actor=user)[0]
        self.assertTrue(real_login(self.client, "sam", code))

    def test_wrong_authenticator_codes_never_throttle_backup_codes(self):
        user = staff("sam")
        mfa.generate_backup_codes(user, actor=user)
        device = TOTPDevice.objects.get(user=user)
        wrong = "000000" if current_totp_code(device) != "000000" else "111111"
        self.assertFalse(real_login(self.client, "sam", wrong))
        self.assertEqual(StaticDevice.objects.get(user=user).throttling_failure_count, 0)


class AuthenticatorServiceTests(TestCase):
    def test_enrollment_needs_the_right_code_before_it_counts(self):
        user = staff("sam")
        pending = mfa.start_authenticator_enrollment(user)
        self.assertEqual(mfa.authenticators(user).count(), 1)  # pending isn't usable yet

        with self.assertRaises(mfa.MfaError):
            mfa.confirm_authenticator(user, code="000000" if current_totp_code(pending) != "000000" else "111111", name="New phone")
        self.assertEqual(mfa.authenticators(user).count(), 1)

        device = mfa.confirm_authenticator(user, code=current_totp_code(pending), name="New phone")
        self.assertEqual((device.pk, device.name, device.confirmed), (pending.pk, "New phone", True))
        self.assertEqual(mfa.authenticators(user).count(), 2)
        self.assertTrue(AuditLog.objects.filter(action="mfa_authenticator_added").exists())

    def test_restarting_enrollment_discards_the_abandoned_attempt(self):
        user = staff("sam")
        first = mfa.start_authenticator_enrollment(user)
        mfa.start_authenticator_enrollment(user)
        self.assertFalse(TOTPDevice.objects.filter(pk=first.pk).exists())
        self.assertEqual(TOTPDevice.objects.filter(user=user, confirmed=False).count(), 1)

    def test_cannot_remove_the_only_authenticator(self):
        user = staff("sam")
        only = mfa.authenticators(user).get()
        with self.assertRaises(mfa.MfaError):
            mfa.remove_authenticator(user, only.pk)
        extra = TOTPDevice.objects.create(user=user, name="spare", confirmed=True)
        mfa.remove_authenticator(user, only.pk)
        self.assertEqual(list(mfa.authenticators(user)), [extra])
        self.assertTrue(AuditLog.objects.filter(action="mfa_authenticator_removed").exists())

    def test_cannot_remove_someone_elses_authenticator(self):
        sam, other = staff("sam"), staff("other")
        TOTPDevice.objects.create(user=sam, name="spare", confirmed=True)
        with self.assertRaises(mfa.MfaError):
            mfa.remove_authenticator(sam, mfa.authenticators(other).get().pk)


class SecurityPageTests(TestCase):
    def test_pages_need_a_verified_login(self):
        staff("sam")
        self.client.login(username="sam", password="testpass123")  # password only
        response = self.client.get("/admin/security/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.headers["Location"])

    def test_overview_shows_the_colleague_section_only_to_superusers(self):
        staff("sam")
        login_with_otp(self.client, username="sam", password="testpass123")
        html = self.client.get("/admin/security/").content.decode()
        self.assertIn('data-security="backup-codes"', html)
        self.assertNotIn('data-security="staff"', html)

        staff("root", group=None, superuser=True)
        login_with_otp(self.client, username="root", password="testpass123")
        html = self.client.get("/admin/security/").content.decode()
        self.assertIn('data-security="staff"', html)
        self.assertIn("sam", html)

    def test_creating_backup_codes_needs_the_password_and_shows_codes_once_uncached(self):
        user = staff("sam")
        login_with_otp(self.client, username="sam", password="testpass123")

        response = self.client.post("/admin/security/backup-codes/", {"password": "wrong"})
        self.assertNotContains(response, 'data-security="codes"')
        self.assertEqual(mfa.backup_codes_remaining(user), 0)

        response = self.client.post("/admin/security/backup-codes/", {"password": "testpass123"})
        self.assertContains(response, 'data-security="codes"')
        self.assertIn("no-store", response["Cache-Control"])
        for token in StaticToken.objects.filter(device__user=user).values_list("token", flat=True):
            self.assertContains(response, token)

    def test_adding_a_new_phone_end_to_end(self):
        user = staff("sam")
        login_with_otp(self.client, username="sam", password="testpass123")

        before = mfa.authenticators(user).count()  # login_with_otp adds its own device
        page = self.client.get("/admin/security/authenticators/add/")
        self.assertContains(page, "<svg")
        pending = mfa.pending_authenticator(user)

        # A wrong code keeps the same QR/secret on screen.
        self.client.post("/admin/security/authenticators/add/", {"name": "New phone", "code": "000000" if current_totp_code(pending) != "000000" else "111111"})
        self.assertEqual(mfa.pending_authenticator(user).pk, pending.pk)

        response = self.client.post(
            "/admin/security/authenticators/add/",
            {"name": "New phone", "code": current_totp_code(pending)},
        )
        self.assertRedirects(response, "/admin/security/", fetch_redirect_response=False)
        self.assertEqual(mfa.authenticators(user).count(), before + 1)

        # The new phone really works at the real login form -- with its
        # *next* code: django-otp rightly rejects reusing the code that
        # just confirmed it (replay protection).
        self.client.logout()
        new_device = mfa.authenticators(user).get(name="New phone")
        self.assertFalse(real_login(self.client, "sam", current_totp_code(new_device)))
        # That rejection also starts django-otp's brief per-device lockout,
        # which a person waiting ~30s for the next code never notices.
        new_device.refresh_from_db()
        new_device.throttle_reset()
        self.assertTrue(real_login(self.client, "sam", next_totp_code(new_device)))

    def test_removing_needs_the_password(self):
        user = staff("sam")
        spare = TOTPDevice.objects.create(user=user, name="old phone", confirmed=True)
        login_with_otp(self.client, username="sam", password="testpass123")
        url = f"/admin/security/authenticators/{spare.pk}/remove/"
        self.client.post(url, {"password": "wrong"})
        self.assertTrue(TOTPDevice.objects.filter(pk=spare.pk).exists())
        self.client.post(url, {"password": "testpass123"})
        self.assertFalse(TOTPDevice.objects.filter(pk=spare.pk).exists())

    def test_full_recovery_of_a_locked_out_colleague(self):
        locked_out = staff("sam")
        TOTPDevice.objects.filter(user=locked_out).delete()  # phone lost, no backup codes
        self.assertFalse(real_login(self.client, "sam", "123456"))

        # Administrators can't issue recovery codes...
        staff("admin1", group="Administrator")
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.assertEqual(self.client.get(f"/admin/security/recovery/{locked_out.pk}/").status_code, 403)

        # ...a superuser can, after confirming their own password.
        staff("root", group=None, superuser=True)
        login_with_otp(self.client, username="root", password="testpass123")
        response = self.client.post(f"/admin/security/recovery/{locked_out.pk}/", {"password": "testpass123"})
        self.assertContains(response, "in person or by phone")
        code = StaticToken.objects.filter(device__user=locked_out).first().token

        # The colleague logs in with one and enrolls a new phone themselves.
        self.client.logout()
        self.assertTrue(real_login(self.client, "sam", code))
        self.client.get("/admin/security/authenticators/add/")
        pending = mfa.pending_authenticator(locked_out)
        self.client.post("/admin/security/authenticators/add/", {"name": "Replacement", "code": current_totp_code(pending)})
        self.assertEqual(mfa.authenticators(locked_out).count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="mfa_recovery_codes_issued").exists())


class DashboardBackupCodeWarningTests(TestCase):
    def test_warning_until_backup_codes_exist(self):
        user = staff("sam")
        login_with_otp(self.client, username="sam", password="testpass123")
        self.assertContains(self.client.get("/admin/"), 'data-dashboard="mfa-warning"')
        mfa.generate_backup_codes(user, actor=user)
        self.assertNotContains(self.client.get("/admin/"), 'data-dashboard="mfa-warning"')

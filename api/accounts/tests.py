from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from transportation.models import TripRequest

from .otp_test_utils import login_with_otp


class RoleSeedingTests(TestCase):
    """
    Runs against the test database, which is migrated fresh (all apps,
    one pass) on every test run -- the exact scenario that originally
    exposed a bug where permissions for models introduced in the same
    migration run weren't available yet to the role-seeding migration.
    """

    def test_dispatcher_group_exists_with_expected_permissions(self):
        dispatcher = Group.objects.get(name="Dispatcher")
        codenames = set(dispatcher.permissions.values_list("codename", flat=True))
        self.assertEqual(
            codenames,
            {
                "view_triprequest",
                "change_triprequest",
                "view_passenger",
                "change_passenger",
                "add_passenger",
                "view_driver",
                "view_vehicle",
                "view_document",
                "view_payer",
                "view_compliancerecord",
                "view_trip",
                "change_trip",
                "add_trip",
            },
        )

    def test_administrator_group_exists_with_expected_permissions(self):
        administrator = Group.objects.get(name="Administrator")
        codenames = set(administrator.permissions.values_list("codename", flat=True))
        self.assertEqual(
            codenames,
            {
                "view_triprequest",
                "change_triprequest",
                "add_triprequest",
                "delete_triprequest",
                "view_passenger",
                "change_passenger",
                "add_passenger",
                "delete_passenger",
                "view_driver",
                "change_driver",
                "add_driver",
                "delete_driver",
                "view_vehicle",
                "change_vehicle",
                "add_vehicle",
                "delete_vehicle",
                "view_trip",
                "change_trip",
                "add_trip",
                "delete_trip",
                "view_document",
                "change_document",
                "add_document",
                "delete_document",
                "view_payer",
                "change_payer",
                "add_payer",
                "delete_payer",
                "view_invoice",
                "change_invoice",
                "add_invoice",
                "delete_invoice",
                "view_claim",
                "change_claim",
                "add_claim",
                "delete_claim",
                "view_compliancerecord",
                "change_compliancerecord",
                "add_compliancerecord",
                "delete_compliancerecord",
                "view_notificationlog",
                "view_auditlog",
            },
        )

    def test_dispatcher_group_cannot_delete_trip_requests(self):
        dispatcher = Group.objects.get(name="Dispatcher")
        delete_perm = Permission.objects.get(codename="delete_triprequest")
        self.assertNotIn(delete_perm, dispatcher.permissions.all())


class RoleEnforcementTests(TestCase):
    """
    Proves the roles actually restrict access in the admin, not just
    that the Group rows exist.
    """

    def setUp(self):
        self.trip_request = TripRequest.objects.create(
            full_name="Test Passenger",
            phone="817-555-0100",
            email="passenger@example.com",
            pickup_address="1 Main St",
            dropoff_address="2 Clinic Rd",
            requested_datetime="2027-01-01T10:00:00Z",
            service_type="ambulatory",
        )

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_trip_requests_in_admin(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher1", password="testpass123")

        response = self.client.get("/admin/transportation/triprequest/")
        self.assertEqual(response.status_code, 200)

    def test_dispatcher_cannot_delete_trip_requests(self):
        self.make_staff_user(username="dispatcher2", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher2", password="testpass123")

        response = self.client.get(
            f"/admin/transportation/triprequest/{self.trip_request.id}/delete/"
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_user_with_no_group_cannot_view_trip_requests(self):
        User.objects.create_user(username="norole", password="testpass123", is_staff=True)
        login_with_otp(self.client, username="norole", password="testpass123")

        response = self.client.get("/admin/transportation/triprequest/")
        self.assertEqual(response.status_code, 403)

    def test_administrator_can_delete_trip_requests(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        login_with_otp(self.client, username="admin1", password="testpass123")

        response = self.client.post(
            f"/admin/transportation/triprequest/{self.trip_request.id}/delete/",
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TripRequest.objects.filter(id=self.trip_request.id).exists())


class StaffMfaEnforcementTests(TestCase):
    """
    Proves ADR 0014's actual security property: a username/password alone
    is not enough to reach /admin/ -- an unverified OTP device makes a
    staff (even superuser) account behave as if it isn't staff at all.
    `login_with_otp` (used by every other admin test in this project) is
    deliberately NOT used here, since asserting it grants access would
    just be testing the test helper.
    """

    def test_admin_site_requires_otp_verification(self):
        from django.contrib import admin
        from django_otp.admin import OTPAdminSite

        self.assertIsInstance(admin.site, OTPAdminSite)

    def test_admin_site_is_also_the_unfold_theme(self):
        """
        Regression test for a real bug caught during ADR 0016: Unfold's
        default AppConfig (bare "unfold" in INSTALLED_APPS) overwrites
        admin.site with a plain UnfoldAdminSite() in its own ready(),
        silently discarding AngelCareAdminConfig.default_site (and with
        it, MFA enforcement) regardless of app order. Fixed by using
        unfold.apps.BasicAppConfig instead -- this test fails loudly if
        that config setting is ever reverted to the bare "unfold" form.
        """
        from django.contrib import admin

        from .admin_site import AngelCareAdminSite

        self.assertIsInstance(admin.site, AngelCareAdminSite)

    def test_password_only_login_cannot_reach_admin_index(self):
        User.objects.create_superuser(
            username="unverified_super", password="testpass123", email="x@example.com"
        )
        logged_in = self.client.login(username="unverified_super", password="testpass123")
        self.assertTrue(logged_in)

        response = self.client.get("/admin/", follow=False)
        # OTPAdminSite.has_permission() fails without a verified device,
        # so this behaves exactly like an anonymous request: redirected
        # to the login page, not a 200 admin index.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.headers["Location"])

    def test_password_only_login_cannot_reach_a_model_list(self):
        self.make_staff_user(username="unverified_admin", groups=["Administrator"])
        self.client.login(username="unverified_admin", password="testpass123")

        response = self.client.get("/admin/transportation/triprequest/")
        self.assertEqual(response.status_code, 302)

    def test_login_with_otp_helper_grants_real_access(self):
        """
        Sanity check on the shared test helper itself: with a confirmed,
        session-verified device, the same account the previous tests
        proved is blocked can now reach /admin/.
        """
        self.make_staff_user(username="verified_admin", groups=["Administrator"])
        login_with_otp(self.client, username="verified_admin", password="testpass123")

        response = self.client.get("/admin/", follow=False)
        self.assertEqual(response.status_code, 200)

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user


def current_totp_code(device) -> str:
    """The code an authenticator app would show right now for `device`."""
    from django_otp.oath import TOTP

    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    return f"{totp.token():0{device.digits}d}"


class RealLoginFormTests(TestCase):
    """
    End-to-end logins through the actual /admin/login/ form, exactly as a
    person submits it: username, password, and the current code -- no
    device picker (our template doesn't render one) and no test helper.

    Regression for a real production outage: django-otp 1.7.4 stopped
    falling back to "try every device" when no device is chosen, so
    every real staff login failed with "Please select a device." while
    all other tests (which use login_with_otp) stayed green.
    """

    def setUp(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        self.user = User.objects.create_user(
            username="staff1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        self.device = TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)

    def login(self, code):
        return self.client.post(
            "/admin/login/?next=/admin/",
            {"username": "staff1", "password": "testpass123", "otp_token": code},
        )

    def test_correct_code_logs_in(self):
        response = self.login(current_totp_code(self.device))
        self.assertEqual(response.status_code, 302, response.content.decode()[-2000:])
        self.assertEqual(response.headers["Location"], "/admin/")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_wrong_code_is_rejected(self):
        response = self.login("000000" if current_totp_code(self.device) != "000000" else "111111")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_missing_code_is_rejected(self):
        response = self.login("")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 302)


class BootstrapTotpCommandTests(TestCase):
    """
    Covers the out-of-band enrollment command that exists precisely
    because the OTPAdminSite tested above can't be used to create the
    very first device on a deployment.
    """

    def test_creates_a_confirmed_device_usable_for_admin_access(self):
        from io import StringIO

        from django.core.management import call_command
        from django_otp.plugins.otp_totp.models import TOTPDevice

        User.objects.create_user(username="new_admin", password="testpass123", is_staff=True)

        out = StringIO()
        call_command("bootstrap_totp", "new_admin", stdout=out)

        device = TOTPDevice.objects.get(user__username="new_admin", confirmed=True)
        self.assertIn(device.config_url, out.getvalue())

    def test_refuses_to_create_a_second_device_without_replace(self):
        from django.core.management import CommandError, call_command

        User.objects.create_user(username="already_enrolled", password="testpass123", is_staff=True)
        call_command("bootstrap_totp", "already_enrolled")

        with self.assertRaises(CommandError):
            call_command("bootstrap_totp", "already_enrolled")

    def test_replace_swaps_the_device(self):
        from django.core.management import call_command
        from django_otp.plugins.otp_totp.models import TOTPDevice

        User.objects.create_user(username="losing_device", password="testpass123", is_staff=True)
        call_command("bootstrap_totp", "losing_device")
        first_key = TOTPDevice.objects.get(user__username="losing_device").key

        call_command("bootstrap_totp", "losing_device", "--replace")

        devices = TOTPDevice.objects.filter(user__username="losing_device", confirmed=True)
        self.assertEqual(devices.count(), 1)
        self.assertNotEqual(devices.first().key, first_key)

    def test_unknown_username_raises_command_error(self):
        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("bootstrap_totp", "does_not_exist")


class AdminPagesSmokeTests(TestCase):
    """
    Renders every registered admin's changelist and add page (plus the
    change page for any row a migration seeds, e.g. Organization) as a
    superuser. Catches breakage that per-domain tests don't reach: a
    django-unfold or Django upgrade changing template/config APIs, a
    bad `list_display` entry, or missing collected static files (the
    cause of the every-/admin/-page 500 noted in ADR 0016).
    """

    def setUp(self):
        User.objects.create_superuser(username="root", password="testpass123")
        login_with_otp(self.client, username="root", password="testpass123")

    def test_admin_index_renders(self):
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_every_registered_admin_page_renders(self):
        from django.contrib import admin

        for model, model_admin in admin.site._registry.items():
            opts = model._meta
            prefix = f"admin:{opts.app_label}_{opts.model_name}"
            urls = [reverse(f"{prefix}_changelist")]
            if model_admin.has_add_permission(self._request()):
                urls.append(reverse(f"{prefix}_add"))
            existing = model._default_manager.first()
            if existing is not None:
                urls.append(reverse(f"{prefix}_change", args=[existing.pk]))
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def _request(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/")
        request.user = User.objects.get(username="root")
        return request


class CanonicalAdminHostTests(TestCase):
    def test_site_url_host_is_always_an_allowed_host(self):
        """Changing DJANGO_SITE_URL alone must be enough to move the admin."""
        from urllib.parse import urlparse

        from django.conf import settings

        self.assertIn(urlparse(settings.SITE_URL).hostname, settings.ALLOWED_HOSTS)

    def test_bare_address_redirects_to_the_admin(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")

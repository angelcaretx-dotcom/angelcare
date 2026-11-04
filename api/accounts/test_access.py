from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog

from . import access
from .otp_test_utils import login_with_otp
from .test_mfa import staff


class AccessRulesTests(TestCase):
    def setUp(self):
        self.admin = staff("admin1", group="Administrator")
        self.disp = staff("disp1", group="Dispatcher")

    def fresh(self, user):
        return User.objects.get(pk=user.pk)  # no cached permissions

    def test_role_baseline_is_described(self):
        levels = access.describe(self.disp)["levels"]
        self.assertEqual(levels["trips"], access.EDIT)
        self.assertEqual(levels["drivers_vehicles"], access.VIEW)
        self.assertEqual(levels["billing"], access.NONE)

    def test_extra_access_on_top_of_the_role(self):
        access.set_access(
            self.disp, actor=self.admin, role="Dispatcher", active=True,
            levels={"billing": access.VIEW, "drivers_vehicles": access.EDIT},
        )
        user = self.fresh(self.disp)
        self.assertTrue(user.has_perm("billing.view_invoice"))
        self.assertFalse(user.has_perm("billing.change_invoice"))
        self.assertTrue(user.has_perm("drivers.change_driver"))
        self.assertTrue(user.has_perm("vehicles.add_vehicle"))
        self.assertTrue(user.has_perm("trips.change_trip"))  # role still applies
        entry = AuditLog.objects.get(action="staff_access_changed")
        self.assertEqual(entry.after["access"]["billing"], "View")

    def test_levels_at_or_below_the_role_add_nothing_and_logs_cap_at_view(self):
        access.set_access(
            self.disp, actor=self.admin, role="Dispatcher", active=True,
            levels={"trips": access.VIEW, "audit_log": access.FULL},
        )
        user = self.fresh(self.disp)
        self.assertEqual(
            set(user.user_permissions.values_list("codename", flat=True)), {"view_auditlog"}
        )

    def test_removing_extras_and_changing_role(self):
        access.set_access(self.disp, actor=self.admin, role="Dispatcher", active=True, levels={"claims": access.FULL})
        access.set_access(self.disp, actor=self.admin, role="Administrator", active=True, levels={})
        user = self.fresh(self.disp)
        self.assertFalse(user.user_permissions.exists())
        self.assertEqual(access.role_of(user), "Administrator")

    def test_switching_access_off_blocks_sign_in(self):
        access.set_access(self.disp, actor=self.admin, role="Dispatcher", active=False, levels={})
        self.assertFalse(self.client.login(username="disp1", password="testpass123"))

    def test_guardrails(self):
        root = staff("root", group=None, superuser=True)
        for actor, target in ((self.disp, self.admin), (self.admin, self.admin), (self.admin, root)):
            with self.subTest(actor=actor.username, target=target.username):
                with self.assertRaises(access.AccessError):
                    access.set_access(target, actor=actor, role=None, active=False, levels={})
        self.assertTrue(self.fresh(root).is_active)


class AccessPageTests(TestCase):
    def setUp(self):
        self.admin = staff("admin1", group="Administrator")
        self.disp = staff("disp1", group="Dispatcher")
        self.url = reverse("admin:security_staff_access", args=[self.disp.pk])

    def form_data(self, **overrides):
        data = {"active": "on", "role": "Dispatcher", "password": "testpass123"}
        data.update({f"area_{a.key}": "0" for a in access.AREAS})
        data.update(overrides)
        return data

    def test_administrator_grants_access_after_confirming_password(self):
        login_with_otp(self.client, username="admin1", password="testpass123")
        overview = self.client.get(reverse("admin:security"))
        self.assertContains(overview, self.url)
        self.assertContains(self.client.get(self.url), "Role gives: Edit")

        bad = self.client.post(self.url, self.form_data(area_billing="1", password="wrong"))
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(User.objects.get(pk=self.disp.pk).has_perm("billing.view_invoice"))

        ok = self.client.post(self.url, self.form_data(area_billing="1"))
        self.assertRedirects(ok, reverse("admin:security"), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(pk=self.disp.pk).has_perm("billing.view_invoice"))

    def test_dispatcher_cannot_manage_access(self):
        login_with_otp(self.client, username="disp1", password="testpass123")
        other = staff("disp2")
        self.assertEqual(
            self.client.get(reverse("admin:security_staff_access", args=[other.pk])).status_code, 403
        )

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

    def test_online_payment_records_come_with_billing_but_only_as_far_as_they_go(self):
        """Payments are seen, never edited; the settings row is seen and changed (ADR 0050)."""
        # The role: Administrators keep full billing access, with only these on the payment records.
        self.assertEqual(access.describe(self.admin)["levels"]["billing"], access.FULL)
        for perm, held in (("view_payment", True), ("view_paymentevent", True), ("view_paymentterms", True),
                           ("change_paymentterms", True), ("add_payment", False), ("change_payment", False),
                           ("delete_payment", False), ("add_paymentterms", False), ("delete_paymentterms", False),
                           ("change_paymentevent", False), ("delete_paymentevent", False)):
            self.assertEqual(self.admin.has_perm(f"billing.{perm}"), held, perm)
        payment_perms = [f"billing.{action}_{model}" for model in ("payment", "paymentevent", "paymentterms")
                         for action in ("view", "add", "change", "delete")]
        self.assertFalse(any(self.disp.has_perm(perm) for perm in payment_perms))  # Dispatchers get nothing

        # Extra access, area by area: View shows the records, Full adds changing the settings and nothing more.
        access.set_access(self.disp, actor=self.admin, role="Dispatcher", active=True, levels={"billing": access.VIEW})
        user = self.fresh(self.disp)
        self.assertEqual({p for p in payment_perms if user.has_perm(p)},
                         {"billing.view_payment", "billing.view_paymentevent", "billing.view_paymentterms"})
        self.assertEqual(access.describe(user)["levels"]["billing"], access.VIEW)
        access.set_access(self.disp, actor=self.admin, role="Dispatcher", active=True, levels={"billing": access.FULL})
        user = self.fresh(self.disp)
        self.assertEqual({p for p in payment_perms if user.has_perm(p)},
                         {"billing.view_payment", "billing.view_paymentevent", "billing.view_paymentterms",
                          "billing.change_paymentterms"})
        self.assertTrue(user.has_perm("billing.delete_invoice"))
        self.assertEqual(access.describe(user)["levels"]["billing"], access.FULL)
        # Taking the extra away takes the payment records with it.
        access.set_access(self.disp, actor=self.admin, role="Dispatcher", active=True, levels={})
        self.assertFalse(any(self.fresh(self.disp).has_perm(perm) for perm in payment_perms))

    def test_billing_access_given_before_online_payment_existed_still_reads_as_it_was(self):
        """Someone given "Billing: View" earlier holds no payment permission yet: their level must not drop."""
        from django.contrib.auth.models import Permission

        old = Permission.objects.filter(content_type__app_label="billing", codename__in=[
            "view_invoice", "view_ratecard", "view_driverpayplan", "view_quote"])
        self.disp.user_permissions.set(old)
        self.assertEqual(access.describe(self.fresh(self.disp))["levels"]["billing"], access.VIEW)

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

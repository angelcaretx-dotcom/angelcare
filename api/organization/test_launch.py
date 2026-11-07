"""The launch checklist (ADR 0045): live checks that update as data is entered."""

from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from billing.models import DriverPayPlan
from trips.tests import make_driver

from . import launch


def state(items, title_start):
    return next(i for i in items if i.title.startswith(title_start))


class LaunchChecklistTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(username="owner", password="testpass123", email="o@example.com")

    def test_empty_system_is_all_to_do_except_what_only_you_can_check(self):
        items = launch.checklist(self.owner, date.today())
        self.assertEqual(state(items, "At least one active driver").state, launch.TODO)
        self.assertEqual(state(items, "Claims settings complete").state, launch.TODO)
        self.assertEqual(state(items, "You have MFA backup codes").state, launch.TODO)
        self.assertEqual(state(items, "BAAs signed").state, launch.MANUAL)

    def test_a_driver_turns_done_once_credentials_and_pay_plan_are_in(self):
        driver = make_driver(legal_name="Sam Taylor")
        driver.credentials.all().delete()
        items = launch.checklist(self.owner, date.today())
        self.assertIn("Sam Taylor", state(items, "Every driver's license").detail)
        self.assertEqual(state(items, "Every driver has a pay plan").state, launch.TODO)

        from trips.tests import give_all_credentials

        give_all_credentials(driver)
        DriverPayPlan.objects.create(driver=driver, effective_from=date.today() - timedelta(days=1), per_trip=20)
        items = launch.checklist(self.owner, date.today())
        self.assertEqual(state(items, "Every driver's license").state, launch.DONE)
        self.assertEqual(state(items, "Every driver has a pay plan").state, launch.DONE)

    def test_page_is_for_superusers(self):
        login_with_otp(self.client, username="owner", password="testpass123")
        page = self.client.get("/admin/organization/organization/launch/")
        self.assertContains(page, "Launch checklist")
        self.assertContains(page, 'data-state="todo"')
        self.client.logout()
        admin = User.objects.create_user(username="admin1", password="testpass123", is_staff=True)
        admin.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.assertEqual(self.client.get("/admin/organization/organization/launch/").status_code, 403)

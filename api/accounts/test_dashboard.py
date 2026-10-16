import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import unfold
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase

from billing.models import Invoice
from claims.models import Claim
from compliance.models import ComplianceRecord
from notifications.models import NotificationLog
from payers.models import Payer
from transportation.models import TripRequestStatus
from trips.models import Trip, TripStatus
from trips.tests import make_driver, make_passenger, make_trip_request, make_vehicle

from .dashboard import EXPIRY_WARNING_DAYS, build_dashboard
from .otp_test_utils import login_with_otp

CENTRAL = ZoneInfo("America/Chicago")
# Behaviour hooks, not styling: copied verbatim from Unfold's own login
# template, which uses them the same way.
HOOK_CLASSES = {"submit-row", "password-reset-link"}
# Noon Central on a Monday after DST has ended (UTC-6).
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=CENTRAL)
TODAY = date(2026, 11, 2)


def staff(username, group):
    user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return User.objects.get(pk=user.pk)  # fresh instance: no cached perms


def stat(dash, label_start):
    matches = [s for s in dash.stats + dash.finance_stats if s.label.startswith(label_start)]
    return matches[0] if matches else None


def panel(dash, title_start):
    matches = [p for p in dash.panels if p.title.startswith(title_start)]
    return matches[0] if matches else None


class DashboardDataTests(TestCase):
    def setUp(self):
        self.admin = staff("admin1", "Administrator")

    def make_trip(self, pickup, status=TripStatus.SCHEDULED, **request_overrides):
        n = Trip.objects.count()
        return Trip.objects.create(
            trip_request=make_trip_request(passenger=make_passenger(), **request_overrides),
            driver=make_driver(license_number=f"TX-DL-{n}"),
            vehicle=make_vehicle(vin=f"1HGCM82633A12345{n}", license_plate=f"TX-{n}"),
            scheduled_pickup_datetime=pickup,
            status=status,
        )

    def test_new_trip_requests_are_counted_and_queued_oldest_first(self):
        first = make_trip_request(full_name="First In")
        make_trip_request(full_name="Second In")
        make_trip_request(full_name="Already Handled", status=TripRequestStatus.REVIEWED)

        dash = build_dashboard(self.admin, now=NOW)

        self.assertEqual(stat(dash, "New trip requests").value, "2")
        self.assertTrue(stat(dash, "New trip requests").alert)
        queue = panel(dash, "Trip requests waiting")
        self.assertEqual([r.cells[0] for r in queue.rows], ["First In", "Second In"])
        self.assertEqual(queue.rows[0].url, f"/admin/transportation/triprequest/{first.pk}/change/")

    def test_trips_today_uses_the_central_time_day_not_utc(self):
        # 11:30 PM Central is already "tomorrow" in UTC -- still today here.
        self.make_trip(datetime(2026, 11, 2, 23, 30, tzinfo=CENTRAL), full_name="Late Tonight")
        self.make_trip(datetime(2026, 11, 2, 8, 0, tzinfo=CENTRAL), full_name="This Morning")
        self.make_trip(datetime(2026, 11, 3, 0, 30, tzinfo=CENTRAL), full_name="After Midnight")
        self.make_trip(
            datetime(2026, 11, 2, 15, 0, tzinfo=CENTRAL),
            status=TripStatus.CANCELLED,
            full_name="Cancelled",
        )

        dash = build_dashboard(self.admin, now=NOW)

        self.assertEqual(stat(dash, "Trips today").value, "2")
        upcoming = [r.cells[1] for r in panel(dash, "Upcoming trips").rows]
        # Past pickups and cancelled trips aren't "upcoming"; order is by time.
        self.assertEqual(upcoming, ["Late Tonight", "After Midnight"])
        self.assertEqual(panel(dash, "Upcoming trips").rows[0].cells[0], "Mon Nov 2, 11:30 PM")

    def test_upcoming_trips_stop_at_the_window(self):
        self.make_trip(datetime(2026, 11, 9, 9, 0, tzinfo=CENTRAL), full_name="Day Seven")
        self.make_trip(datetime(2026, 11, 10, 9, 0, tzinfo=CENTRAL), full_name="Day Eight")
        names = [r.cells[1] for r in panel(build_dashboard(self.admin, now=NOW), "Upcoming").rows]
        self.assertEqual(names, ["Day Seven"])

    def test_expiring_credentials_list_soonest_first_and_flag_expired(self):
        make_driver(legal_name="Expired Ed", license_number="A", license_expiration_date=TODAY - timedelta(days=3))
        make_driver(legal_name="Soon Sue", license_number="B", license_expiration_date=TODAY + timedelta(days=10))
        make_driver(legal_name="Fine Fred", license_number="C", license_expiration_date=TODAY + timedelta(days=EXPIRY_WARNING_DAYS + 1))
        make_driver(legal_name="Gone Gina", license_number="D", status="terminated", license_expiration_date=TODAY - timedelta(days=1))
        make_vehicle(
            registration_expiration_date=TODAY + timedelta(days=EXPIRY_WARNING_DAYS),
            inspection_expiration_date=TODAY,
        )
        ComplianceRecord.objects.create(
            name="General Liability", record_type="insurance_policy",
            expiration_date=TODAY + timedelta(days=5),
        )

        rows = panel(build_dashboard(self.admin, now=NOW), "Credentials expiring").rows

        self.assertEqual(
            [(r.cells[0], r.cells[1], r.cells[3]) for r in rows],
            [
                ("Expired Ed", "Driver's license", "Expired 3 days ago"),
                ("Ford Transit (TX-ABC123)", "Inspection", "Expires today"),
                ("General Liability", "Insurance Policy", "In 5 days"),
                ("Soon Sue", "Driver's license", "In 10 days"),
                ("Ford Transit (TX-ABC123)", "Registration", f"In {EXPIRY_WARNING_DAYS} days"),
            ],
        )
        self.assertEqual([r.alert for r in rows], [True, False, False, False, False])

    def test_failed_emails_only_count_the_last_week(self):
        for days_ago, status in [(1, "failed"), (6, "failed"), (8, "failed"), (1, "sent")]:
            log = NotificationLog.objects.create(
                notification_type="new_trip_request_staff", recipient="x@example.com", status=status
            )
            NotificationLog.objects.filter(pk=log.pk).update(created_at=NOW - timedelta(days=days_ago))
        self.assertEqual(stat(build_dashboard(self.admin, now=NOW), "Failed emails").value, "2")

    def test_finance_figures(self):
        trip = self.make_trip(NOW + timedelta(days=1))
        payer = Payer.objects.create(name="Sample MCO", payer_type="medicaid_mco")
        Invoice.objects.create(trip=trip, amount=Decimal("125.50"), status="sent")
        paid = Invoice.objects.create(trip=self.make_trip(NOW + timedelta(days=2)), amount=Decimal("99"), status="paid")
        Claim.objects.create(invoice=paid, payer=payer, amount_claimed=Decimal("99"), status="denied")

        dash = build_dashboard(self.admin, now=NOW)

        self.assertEqual(stat(dash, "Unpaid invoices").label, "Unpaid invoices (1)")
        self.assertEqual(stat(dash, "Unpaid invoices").value, "$125.50")
        self.assertEqual(stat(dash, "Denied claims").value, "1")
        self.assertTrue(stat(dash, "Denied claims").alert)


class DashboardPermissionTests(TestCase):
    def test_dispatcher_never_sees_finance_or_email_figures(self):
        dash = build_dashboard(staff("dispatch1", "Dispatcher"), now=NOW)
        self.assertEqual(dash.finance_stats, [])
        self.assertIsNone(stat(dash, "Failed emails"))
        # ...but does see the operational sections their role covers.
        self.assertIsNotNone(stat(dash, "New trip requests"))
        self.assertIsNotNone(stat(dash, "Trips today"))
        self.assertIsNotNone(panel(dash, "Credentials expiring"))

    def test_staff_without_any_view_permissions_get_an_empty_dashboard(self):
        user = User.objects.create_user(username="nobody", password="x", is_staff=True)
        dash = build_dashboard(user, now=NOW)
        self.assertEqual((dash.stats, dash.finance_stats, dash.panels), ([], [], []))


class DashboardPageTests(TestCase):
    def test_administrator_home_page_shows_dashboard_and_keeps_app_list(self):
        staff("admin1", "Administrator")
        login_with_otp(self.client, username="admin1", password="testpass123")
        html = self.client.get("/admin/").content.decode()
        self.assertIn('data-dashboard="stats"', html)
        self.assertIn('data-dashboard="finance"', html)
        self.assertIn("No new requests. All caught up.", html)
        self.assertIn("/admin/drivers/driver/", html)  # Unfold's app list still below

    def test_dispatcher_home_page_has_no_finance_section(self):
        staff("dispatch1", "Dispatcher")
        login_with_otp(self.client, username="dispatch1", password="testpass123")
        html = self.client.get("/admin/").content.decode()
        self.assertIn('data-dashboard="stats"', html)
        self.assertNotIn('data-dashboard="finance"', html)
        self.assertNotIn("Unpaid invoices", html)


class DashboardTemplateTests(TestCase):
    def test_every_css_class_used_exists_in_unfolds_stylesheet(self):
        """
        Unfold ships precompiled Tailwind: a class it doesn't already
        use simply has no CSS. Guards the template (and Unfold upgrades)
        against silently unstyled dashboards.
        """
        stylesheet = Path(unfold.__file__).parent.joinpath(
            "static/unfold/css/styles.css"
        ).read_text(encoding="utf-8")
        # Every project admin template: the dashboard, the login page,
        # and the My security pages (ADR 0024).
        templates = sorted(Path(settings.BASE_DIR, "templates/admin").rglob("*.html"))
        self.assertGreaterEqual(len(templates), 6)
        missing = {}
        for template in templates:
            classes = set()
            for attr in re.findall(r'class="([^"]*)"', template.read_text(encoding="utf-8")):
                attr = re.sub(r"{%.*?%}|{{.*?}}", " ", attr)
                classes.update(attr.split())
            if template.name == "index.html":
                self.assertIn("md:grid-cols-2", classes)  # extraction sanity check
            absent = sorted(
                c for c in classes - HOOK_CLASSES if not _css_has_class(stylesheet, c)
            )
            if absent:
                missing[str(template.relative_to(settings.BASE_DIR))] = absent
        self.assertEqual(missing, {})

    def test_css_class_check_itself(self):
        css = ".lg\\:grid-cols-1{x}.text-sm,.dark\\:text-red-400:where(.dark *){y}"
        self.assertTrue(_css_has_class(css, "lg:grid-cols-1"))
        self.assertTrue(_css_has_class(css, "dark:text-red-400"))
        self.assertTrue(_css_has_class(css, "text-sm"))
        self.assertFalse(_css_has_class(css, "lg:grid-cols-13"))
        self.assertFalse(_css_has_class(css, "text-s"))


def _css_has_class(stylesheet: str, name: str) -> bool:
    """True if `.name` (CSS-escaped) appears as a complete class selector."""
    selector = "." + name
    for char in "/:[]*":
        selector = selector.replace(char, "\\" + char)
    return re.search(re.escape(selector) + r"(?![\w-])", stylesheet) is not None

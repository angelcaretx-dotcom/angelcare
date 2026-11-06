"""Trip profit and 1099 year-end totals (ADR 0041)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User

from accounts.otp_test_utils import login_with_otp
from trips.models import TripStatus
from trips.tests import make_driver, make_vehicle
from vehicles.models import VehicleCheck

from . import reports
from .models import DriverPayPlan, Invoice, InvoiceStatus, RateCard
from .test_rates import DAY, RatesTestCase, at


class ProfitTests(RatesTestCase):
    def setUp(self):
        super().setUp()
        self.driver = make_driver()
        DriverPayPlan.objects.create(driver=self.driver, effective_from=date(2027, 1, 1), per_trip=Decimal("20"),
                                     per_mile=Decimal("0.50"))

    def test_charge_minus_pay_minus_mileage_by_payer(self):
        self.trip(driver=self.driver, billable_miles=Decimal("10"))  # card: 45 + 5 × 2.50 = 57.50; pay 20 + 5
        invoiced = self.trip(driver=self.driver, billable_miles=Decimal("4"), pickup=at(11))
        Invoice.objects.create(trip=invoiced, payer=self.mtm, amount=Decimal("50.00"), status=InvoiceStatus.SENT)
        result = reports.profit(DAY, DAY, cost_per_mile=Decimal("0.25"))
        mtm = result["payers"][0]
        self.assertEqual((mtm["payer"], mtm["counted"], mtm["charge"], mtm["pay"], mtm["mileage"]),
                         ("MTM", 2, Decimal("107.50"), Decimal("47.00"), Decimal("3.50")))
        self.assertEqual(mtm["profit"], Decimal("57.00"))
        self.assertEqual(mtm["margin"], Decimal("53.0"))
        self.assertEqual(result["total"], Decimal("57.00"))

    def test_missing_numbers_leave_a_trip_out_and_say_why(self):
        self.trip(driver=self.driver)  # no miles: per-mile card and pay can't be worked out
        self.trip(pickup=at(11), billable_miles=Decimal("1"))  # no driver
        result = reports.profit(DAY, DAY)
        self.assertEqual(result["payers"][0]["counted"], 0)
        notes = [note for row in result["incomplete"] for note in row["notes"]]
        self.assertTrue(any("charge unknown" in n for n in notes))
        self.assertTrue(any("no driver" in n for n in notes))

    def test_private_pay_and_hourly_pay_off_the_total(self):
        RateCard.objects.create(name="Private pay", effective_from=date(2026, 1, 1), base_wheelchair=Decimal("60"))
        self.trip(payer=None, driver=self.driver, billable_miles=Decimal("2"))  # 60 - 21 = 39
        plan = self.driver.pay_plans.get()
        plan.hourly = Decimal("10")
        plan.save()
        VehicleCheck.objects.create(vehicle=make_vehicle(), driver=self.driver, service_date=DAY, start_odometer=1,
                                    checked_at=at(8), end_odometer=50, ended_at=at(10))
        result = reports.profit(DAY, DAY)
        self.assertEqual(result["payers"][0]["payer"], reports.PRIVATE)
        self.assertEqual(result["payers"][0]["profit"], Decimal("39.00"))
        self.assertEqual((result["hourly"], result["total"]), (Decimal("20.00"), Decimal("19.00")))


class YearEndTests(RatesTestCase):
    def test_contractors_only_with_the_threshold(self):
        contractor = make_driver(legal_name="Casey Contractor", employment_type="contractor")
        employee = make_driver(legal_name="Erin Employee", license_number="TX-EMP-2")
        for driver in (contractor, employee):
            DriverPayPlan.objects.create(driver=driver, effective_from=date(2027, 1, 1), per_trip=Decimal("700"))
            self.trip(driver=driver)
        rows = reports.year_end(2027, reports.default_threshold(2027))
        self.assertEqual([(r["driver"], r["total"], r["reportable"]) for r in rows],
                         [(contractor, Decimal("700.00"), False)])
        self.assertTrue(reports.year_end(2027, Decimal("600"))[0]["reportable"])
        self.assertEqual(reports.default_threshold(2025), Decimal("600"))
        self.assertEqual(reports.default_threshold(2026), Decimal("2000"))
        self.assertEqual(reports.year_end(2026, Decimal("600")), [])


class ReportPageTests(RatesTestCase):
    def login(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")

    def test_administrator_sees_both_reports_and_csvs(self):
        self.login("Administrator")
        driver = make_driver(employment_type="contractor")
        DriverPayPlan.objects.create(driver=driver, effective_from=date(2027, 1, 1), per_trip=Decimal("10"))
        self.trip(driver=driver, billable_miles=Decimal("3"))
        query = {"start": DAY.isoformat(), "end": DAY.isoformat()}
        self.assertContains(self.client.get("/admin/billing/invoice/profit/", query), "Profit $35.00 on $45.00 billed")
        csv = self.client.get("/admin/billing/invoice/profit/", dict(query, format="csv"))
        self.assertIn(b"Total,45.00,,,35.00", csv.content)
        page = self.client.get("/admin/billing/driverpayplan/year-end/", {"year": "2027"})
        self.assertContains(page, "threshold $2000")
        self.assertContains(page, "Under threshold")
        csv = self.client.get("/admin/billing/driverpayplan/year-end/", {"year": "2027", "format": "csv"})
        self.assertIn(b"10.00,no", csv.content)
        self.assertEqual(self.client.get("/admin/billing/invoice/profit/").status_code, 200)
        self.assertEqual(self.client.get("/admin/billing/driverpayplan/year-end/").status_code, 200)

    def test_dispatchers_cannot_see_money_reports(self):
        self.login("Dispatcher")
        self.assertEqual(self.client.get("/admin/billing/invoice/profit/").status_code, 403)
        self.assertEqual(self.client.get("/admin/billing/driverpayplan/year-end/").status_code, 403)

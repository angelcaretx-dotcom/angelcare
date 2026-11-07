"""Driver and vehicle spreadsheet import (ADR 0045): checked, reviewed, then only good rows saved."""

import csv
import io
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from vehicles.models import Vehicle

from . import bulk
from .models import CredentialType, Driver, DriverCredential

NEXT_YEAR = (date.today() + timedelta(days=365)).isoformat()
LAST_MONTH = (date.today() - timedelta(days=30)).isoformat()


def sheet(kind, *rows):
    labels = [label for _, label in bulk.COLUMNS[kind]]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(labels)
    for row in rows:
        writer.writerow([row.get(label, "") for label in labels])
    return out.getvalue().encode()


def driver_row(**extra):
    row = {"Legal name": "Sam Taylor", "Phone": "817-555-0155", "Employment type": "contractor",
           "License number": "TX-DL-1", "License expires": NEXT_YEAR}
    for kind, label in CredentialType.choices:
        row[f"{label} done"], row[f"{label} expires"] = LAST_MONTH, NEXT_YEAR
    row.update(extra)
    return row


class CheckTests(TestCase):
    def test_good_driver_rows_carry_their_credentials(self):
        rows = bulk.check(bulk.DRIVERS, sheet(bulk.DRIVERS, driver_row()), "drivers.csv")
        self.assertTrue(rows[0].ok, rows[0].problems)
        self.assertEqual(len(rows[0].credentials), len(CredentialType.values))
        made = bulk.create(bulk.DRIVERS, rows, user=None)
        self.assertEqual(made[0].employment_type, "contractor")
        self.assertEqual(DriverCredential.objects.filter(driver=made[0]).count(), len(CredentialType.values))
        self.assertTrue(AuditLog.objects.filter(resource_type="Driver", action="created").exists())

    def test_bad_rows_say_why_and_are_never_saved(self):
        bad = driver_row(**{"Employment type": "part time", "License expires": "soon",
                            "Drug and alcohol screen done": ""})
        rows = bulk.check(bulk.DRIVERS, sheet(bulk.DRIVERS, bad), "drivers.csv")
        problems = " ".join(rows[0].problems)
        self.assertIn("employee or contractor", problems)
        self.assertIn("License expires", problems)
        self.assertIn("needs the date it was done", problems)
        self.assertEqual(bulk.create(bulk.DRIVERS, rows, user=None), [])
        self.assertFalse(Driver.objects.exists())

    def test_a_driver_or_vehicle_already_on_file_is_skipped(self):
        bulk.create(bulk.DRIVERS, bulk.check(bulk.DRIVERS, sheet(bulk.DRIVERS, driver_row()), "d.csv"), user=None)
        again = bulk.check(bulk.DRIVERS, sheet(bulk.DRIVERS, driver_row(**{"License number": "tx-dl-1"})), "d.csv")
        self.assertIn("already on file", again[0].skipped)

    def test_vehicles_with_and_without_insurance(self):
        van = {"VIN": "2C4RDGBG1GR123456", "Year": "2016", "Make": "Dodge", "Model": "Grand Caravan",
               "License plate": "abc1234", "Wheelchair accessible": "yes", "Passenger seats": "4",
               "Registration expires": NEXT_YEAR, "Inspection expires": NEXT_YEAR}
        rows = bulk.check(bulk.VEHICLES, sheet(bulk.VEHICLES, van, dict(van, VIN="SHORT")), "vehicles.csv")
        self.assertTrue(rows[0].ok, rows[0].problems)
        self.assertIn("17 characters", " ".join(rows[1].problems))
        made = bulk.create(bulk.VEHICLES, rows, user=None)
        self.assertEqual(len(made), 1)
        vehicle = Vehicle.objects.get()
        self.assertEqual((vehicle.wheelchair_capable, vehicle.insurance_expiration_date, vehicle.license_plate),
                         (True, None, "ABC1234"))

    def test_the_wrong_template_is_refused(self):
        from intake.extract import ExtractError

        with self.assertRaisesMessage(ExtractError, "drivers template"):
            bulk.check(bulk.DRIVERS, sheet(bulk.VEHICLES, {"VIN": "x"}), "oops.csv")


class AdminTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="admin1", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")

    def test_check_then_add(self):
        upload = SimpleUploadedFile("drivers.csv", sheet(bulk.DRIVERS, driver_row()))
        page = self.client.post("/admin/drivers/driver/import/", {"kind": "drivers", "file": upload})
        self.assertContains(page, "1 of 1 ready to add")
        self.assertFalse(Driver.objects.exists())
        page = self.client.post("/admin/drivers/driver/import/", {"kind": "drivers", "action": "create"})
        self.assertContains(page, "1 added")
        self.assertEqual(Driver.objects.get().legal_name, "Sam Taylor")
        template = self.client.get("/admin/drivers/driver/import/template/vehicles/")
        self.assertIn("Insurance expires", template.content.decode())

    def test_dispatchers_cannot_import(self):
        self.client.logout()
        user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="disp", password="testpass123")
        self.assertEqual(self.client.get("/admin/drivers/driver/import/").status_code, 403)

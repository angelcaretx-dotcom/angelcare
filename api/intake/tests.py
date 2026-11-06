"""Trip intake (ADR 0038): any sender's CSV, Excel or text PDF into reviewed Trips."""

import io
import shutil
import tempfile
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from passengers.models import Passenger
from payers.models import Payer
from trips.models import StandingOrder, Trip, TripLeg
from trips.tests import make_passenger, make_vehicle

from . import services
from .models import ImportBatch, MappingTemplate, RowStatus

CENTRAL = ZoneInfo("America/Chicago")
DAY = date.today() + timedelta(days=30)
US = DAY.strftime("%m/%d/%Y")
TEMP_MEDIA = tempfile.mkdtemp()

# A broker-style export with columns nobody has mapped before.
BROKER_CSV = f"""Leg Ref,Member,Member Ph,Svc Dt,PU Tm,From,To,LOS,Auth #,Return
MX-1001,"LEE, MORGAN",(817) 555-0199,{US},10:00 AM,"12 Elm St, Fort Worth, TX 76104","1400 8th Ave, Fort Worth, TX 76104",WC,PA-555,2:30 PM
MX-1002,Pat Diaz,817-555-0123,{US},7:15 AM,"9 Oak Rd, Arlington, TX 76010","2 Clinic Rd, Arlington, TX 76011",AMB,PA-556,
"""


def upload(name, content, *, source="mtm", user=None, payer=None):
    return services.upload(SimpleUploadedFile(name, content), source=source, payer=payer, user=user)


def map_broker(batch):
    batch.field_map = {
        "trip_id": "Leg Ref", "rider_name": "Member", "rider_phone": "Member Ph", "pickup_date": "Svc Dt",
        "pickup_time": "PU Tm", "pickup_address": "From", "dropoff_address": "To", "service_type": "LOS",
        "prior_auth": "Auth #", "return_time": "Return",
    }
    batch.save()
    services.apply_mapping(batch)


def excel(rows):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def order_pdf(lines):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class IntakeTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user(username="disp", password="testpass123", is_staff=True)
        self.user.groups.add(Group.objects.get(name="Dispatcher"))


class BrokerCsvTests(IntakeTestCase):
    def test_unfamiliar_columns_map_and_create_correct_trips(self):
        batch = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        self.assertIsNone(batch.template)
        map_broker(batch)
        rows = {r.number: r for r in batch.rows.all()}
        # Both riders are new to us: a person confirms, then the trips are made.
        self.assertTrue(all(r.status == RowStatus.REVIEW and not r.hard_problems for r in rows.values()))
        for row in rows.values():
            services.create_trips(row, user=self.user)

        trip = Trip.objects.get(broker_trip_number="MX-1001")
        self.assertEqual(trip.passenger.legal_name, "Morgan Lee")
        self.assertEqual((trip.service_type, trip.prior_authorization_number, trip.leg), ("wheelchair", "PA-555", TripLeg.OUTBOUND))
        self.assertEqual(trip.pickup_address, "12 Elm St, Fort Worth, TX 76104")
        back = trip.return_trip
        self.assertEqual(back.pickup_address, "1400 8th Ave, Fort Worth, TX 76104")
        self.assertEqual(timezone.localtime(back.scheduled_pickup_datetime, CENTRAL).time(), time(14, 30))
        self.assertEqual(Trip.objects.get(broker_trip_number="MX-1002").service_type, "ambulatory")
        self.assertEqual(batch.counts()["created"], 2)
        self.assertTrue(AuditLog.objects.filter(action="imported", resource_type="ImportBatch").exists())

    def test_ten_am_in_the_file_is_ten_am_on_the_day_board(self):
        """Mandatory regression (the 2026-11-05 six-hour bug)."""
        batch = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        map_broker(batch)
        services.create_trips(batch.rows.get(number=2), user=self.user)
        trip = Trip.objects.get(broker_trip_number="MX-1001")
        self.assertEqual(timezone.localtime(trip.scheduled_pickup_datetime, CENTRAL).time(), time(10, 0))
        login_with_otp(self.client, username="disp", password="testpass123")
        board = self.client.get("/admin/trips/trip/board/", {"date": DAY.isoformat()}).content.decode()
        self.assertIn("10:00 AM", board)
        self.assertNotIn("4:00 AM", board)

    def test_reimporting_the_same_file_creates_nothing(self):
        first = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        map_broker(first)
        for row in first.rows.all():
            services.create_trips(row, user=self.user)
        again = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        map_broker(again)
        self.assertEqual(set(again.rows.values_list("status", flat=True)), {RowStatus.DUPLICATE})
        self.assertEqual(services.create_ready(again, user=self.user), (0, 0, []))
        self.assertEqual(Trip.objects.count(), 3)  # two rides + one ride back, still

    def test_saved_mapping_maps_the_senders_next_file(self):
        batch = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        map_broker(batch)
        MappingTemplate.objects.create(name="MTM export", source="mtm", headers=batch.headers, field_map=batch.field_map)
        nxt = upload("mtm-week2.csv", BROKER_CSV.replace("MX-10", "MX-20").encode(), user=self.user)
        self.assertEqual(nxt.template.name, "MTM export")
        self.assertTrue(all(r.values.get("rider_phone") for r in nxt.rows.all()))

    def test_known_rider_with_clean_data_is_ready_straight_away(self):
        make_passenger(legal_name="Morgan Lee", phone="817-555-0199")
        batch = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        map_broker(batch)
        self.assertEqual(batch.rows.get(number=2).status, RowStatus.READY)
        self.assertEqual(services.create_ready(batch, user=self.user), (1, 2, []))  # the ride and its ride back
        self.assertEqual(Passenger.objects.filter(legal_name="Morgan Lee").count(), 1)


class BadDataGoesToReviewTests(IntakeTestCase):
    def rows(self, lines):
        header = "Trip ID,Name,Phone,Date,Time,Pickup,Dropoff,Service\n"
        batch = upload("bad.csv", (header + "\n".join(lines)).encode(), user=self.user)
        batch.field_map = {"trip_id": "Trip ID", "rider_name": "Name", "rider_phone": "Phone", "pickup_date": "Date",
                           "pickup_time": "Time", "pickup_address": "Pickup", "dropoff_address": "Dropoff",
                           "service_type": "Service"}
        batch.save()
        services.apply_mapping(batch)
        return {r.number: r for r in batch.rows.all()}

    def test_never_guessed(self):
        past = (date.today() - timedelta(days=3)).strftime("%m/%d/%Y")
        good_a, good_b = '"1 A St, Fort Worth, TX 76104"', '"2 B St, Fort Worth, TX 76104"'
        rows = self.rows([
            f"T1,Ann Ray,817-555-0101,{US},,{good_a},{good_b},WC",                   # no time
            f'T2,Ann Ray,817-555-0101,{US},9:00,"1 A St, Fort Worth, TX 7610",{good_b},WC',  # bad ZIP
            f"T3,Ann Ray,817-555-0101,{US},9:00,{good_a},{good_a},WC",              # same address
            f"T4,Ann Ray,817-555-0101,{past},9:00,{good_a},{good_b},WC",            # past
            f"T5,Ann Ray,817-555-0101,{US},9:00,{good_a},{good_b},STRETCHER",       # stretcher
            f"T6,Ann Ray,817-555-0101,{US},9:00,{good_a},{good_b},BUS",             # unknown service
        ])
        expected = {2: "No pickup time", 3: "ZIP", 4: "same address", 5: "in the past", 6: "Stretcher", 7: "isn't ambulatory"}
        for number, words in expected.items():
            row = rows[number]
            self.assertEqual(row.status, RowStatus.REVIEW, number)
            self.assertTrue(any(words in p for p in row.hard_problems), (number, row.problems))
            with self.assertRaises(Exception):
                services.create_trips(row, user=self.user)
        self.assertEqual(Trip.objects.count(), 0)

    def test_fixing_a_row_in_review_then_approving(self):
        row = self.rows([f'T9,Ann Ray,817-555-0101,{US},,"1 A St, Fort Worth, TX 76104","2 B St, Fort Worth, TX 76104",WC'])[2]
        login_with_otp(self.client, username="disp", password="testpass123")
        values = dict(row.values, pickup_time="11:45 AM", action="approve")
        response = self.client.post(f"/admin/intake/importbatch/row/{row.pk}/", values)
        self.assertEqual(response.status_code, 302)
        trip = Trip.objects.get(broker_trip_number="T9")
        self.assertEqual(timezone.localtime(trip.scheduled_pickup_datetime, CENTRAL).time(), time(11, 45))

    def test_rejected_row_makes_no_trip(self):
        row = self.rows([f"T8,Ann Ray,817-555-0101,{US},,x,y,WC"])[2]
        login_with_otp(self.client, username="disp", password="testpass123")
        self.client.post(f"/admin/intake/importbatch/row/{row.pk}/", {"action": "reject"})
        row.refresh_from_db()
        self.assertEqual(row.status, RowStatus.REJECTED)
        self.assertEqual(Trip.objects.count(), 0)


class ReportQuirkTests(IntakeTestCase):
    """Owner's acceptance test, 2026-11-06 (Finding 3)."""

    rows = BadDataGoesToReviewTests.rows

    def test_no_state_column_means_texas(self):
        batch = upload("parts.csv", ("Name,Phone,Date,Time,Street,City,Zip,To Street,To City,To Zip,Service\n"
                                     f"Ann Ray,817-555-0101,{US},9:00,1 A St,Fort Worth,76104,2 B St,Fort Worth,76104,WC\n").encode(),
                       user=self.user)
        batch.field_map = {"rider_name": "Name", "rider_phone": "Phone", "pickup_date": "Date", "pickup_time": "Time",
                           "pickup_street": "Street", "pickup_city": "City", "pickup_zip": "Zip",
                           "dropoff_street": "To Street", "dropoff_city": "To City", "dropoff_zip": "To Zip",
                           "service_type": "Service"}
        batch.save()
        services.apply_mapping(batch)
        row = batch.rows.get()
        self.assertEqual(row.hard_problems, [], row.problems)
        self.assertEqual(row.data["pickup_address"], "1 A St, Fort Worth, TX 76104")

    def test_a_title_row_above_the_header_is_skipped(self):
        content = excel([
            ["Baylor discharge manifest - week of Nov 9", None, None],
            [None, None, None],
            ["Patient", "Phone", "Pickup Address"],
            ["Ann Ray", "8175550101", "1 A St\nFort Worth, TX 76104"],
        ])
        batch = upload("manifest.xlsx", content, source="facility", user=self.user)
        self.assertEqual(batch.headers, ["Patient", "Phone", "Pickup Address"])
        self.assertEqual(batch.rows.count(), 1)
        values = batch.rows.get().raw
        from .normalize import split_address

        self.assertEqual(split_address(values["Pickup Address"])["city"], "Fort Worth")

    def test_a_row_rejected_before_says_so_on_reimport(self):
        line = f'T8,Ann Ray,817-555-0101,{US},9:00,"1 A St, Fort Worth, TX 76104","2 B St, Fort Worth, TX 76104",WC'
        row = self.rows([line])[2]
        services.reject(row, user=self.user)
        again = self.rows([line])[2]
        self.assertEqual(again.status, RowStatus.REVIEW)
        self.assertIn("was rejected in import", again.problems[0]["message"])


class FacilityExcelTests(IntakeTestCase):
    def test_manifest_splits_addresses_and_service_types(self):
        content = excel([
            ["Patient Last", "Patient First", "Phone", "Appt Date", "Pickup Time", "Pickup Street", "Pickup City",
             "Pickup State", "Pickup Zip", "Dest Street", "Dest City", "Dest State", "Dest Zip", "Mode", "Notes"],
            ["Ray", "Ann", "8175550101", DAY, time(8, 30), "1 A St", "Fort Worth", "TX", "76104",
             "2 B St", "Fort Worth", "TX", "76104", "Wheelchair", "Uses a walker"],
            ["Cole", "Jo", "8175550102", DAY, time(13, 0), "5 C St", "Keller", "TX", 76248,
             "6 D St", "Keller", "TX", 76248, "Ambulatory", ""],
        ])
        batch = upload("baylor.xlsx", content, source="facility", user=self.user)
        guessed = batch.field_map
        self.assertEqual(guessed.get("pickup_city"), "Pickup City")
        self.assertEqual(guessed.get("rider_last"), "Patient Last")
        batch.field_map = dict(guessed, dropoff_street="Dest Street", dropoff_city="Dest City",
                               dropoff_state="Dest State", dropoff_zip="Dest Zip", service_type="Mode",
                               pickup_date="Appt Date")
        batch.save()
        services.apply_mapping(batch)
        for row in batch.rows.all():
            self.assertEqual(row.hard_problems, [], row.problems)
            services.create_trips(row, user=self.user)
        ann = Trip.objects.get(passenger__legal_name="Ann Ray")
        self.assertEqual(ann.pickup_address, "1 A St, Fort Worth, TX 76104")
        self.assertEqual(ann.service_type, "wheelchair")
        self.assertIn("Instructions: Uses a walker", ann.notes)
        jo = Trip.objects.get(passenger__legal_name="Jo Cole")
        self.assertEqual((jo.service_type, jo.dropoff_address), ("ambulatory", "6 D St, Keller, TX 76248"))
        self.assertEqual(timezone.localtime(jo.scheduled_pickup_datetime, CENTRAL).time(), time(13, 0))


class PdfOrderTests(IntakeTestCase):
    def test_order_form_with_a_return_leg_makes_two_linked_trips(self):
        content = order_pdf([
            "Transportation Order",
            "Order Number: HH-778",
            "Patient Name: Robin Diaz",
            "Phone: 817-555-0144",
            f"Date of Service: {US}",
            "Pickup Time: 9:15 AM",
            "Pickup Address: 40 Pine St, Fort Worth, TX 76104",
            "Destination: 700 Main St, Fort Worth, TX 76102",
            "Level of Service: Ambulatory",
            "Return Time: Will Call",
        ])
        batch = upload("order.pdf", content, source="hhsc", user=self.user)
        self.assertEqual(batch.rows.count(), 1)
        batch.field_map = dict(batch.field_map, trip_id="Order Number", pickup_time="Pickup Time",
                               dropoff_address="Destination", return_time="Return Time")
        batch.save()
        services.apply_mapping(batch)
        row = batch.rows.get()
        self.assertEqual(row.hard_problems, [], row.problems)
        services.create_trips(row, user=self.user)
        outbound = Trip.objects.get(broker_trip_number="HH-778")
        self.assertEqual(outbound.leg, TripLeg.OUTBOUND)
        self.assertTrue(outbound.return_trip.will_call)
        self.assertEqual(outbound.return_trip.outbound_trip, outbound)

    def test_scanned_pdf_is_refused_clearly(self):
        from fpdf import FPDF

        pdf = FPDF()
        pdf.add_page()
        with self.assertRaises(services.UploadError) as caught:
            upload("scan.pdf", bytes(pdf.output()), user=self.user)
        self.assertIn("scanned", str(caught.exception))


class AutoAssignTests(IntakeTestCase):
    def standing(self, driver):
        passenger = make_passenger(legal_name="Morgan Lee", phone="817-555-0199")
        StandingOrder.objects.create(
            passenger=passenger, service_type="wheelchair", pickup_address="x", dropoff_address="y",
            weekdays=[DAY.weekday()], pickup_time=time(10, 0), start_date=date.today(), driver=driver,
            vehicle=make_vehicle(),
        )

    def import_first_row(self):
        batch = upload("mtm.csv", BROKER_CSV.encode(), user=self.user)
        map_broker(batch)
        services.create_trips(batch.rows.get(number=2), user=self.user)
        return Trip.objects.get(broker_trip_number="MX-1001")

    def test_standing_order_driver_with_credentials_is_assigned(self):
        from trips.tests import make_driver

        driver = make_driver()
        self.standing(driver)
        self.assertEqual(self.import_first_row().driver, driver)

    def test_driver_missing_a_credential_is_never_auto_assigned(self):
        from drivers.models import Driver

        driver = Driver.objects.create(legal_name="No Creds", phone="817-555-0188", employment_type="employee",
                                       license_number="X", license_expiration_date=date.today() + timedelta(days=300))
        self.standing(driver)
        trip = self.import_first_row()
        self.assertIsNone(trip.driver_id)

    def test_no_standing_order_means_unassigned(self):
        make_passenger(legal_name="Morgan Lee", phone="817-555-0199")
        self.assertIsNone(self.import_first_row().driver_id)


class AdminFlowTests(IntakeTestCase):
    def setUp(self):
        super().setUp()
        login_with_otp(self.client, username="disp", password="testpass123")

    def test_upload_map_review_and_report_pages(self):
        response = self.client.post("/admin/intake/importbatch/upload/", {
            "source": "mtm", "payer": "", "file": SimpleUploadedFile("mtm.csv", BROKER_CSV.encode()),
        })
        batch = ImportBatch.objects.get()
        self.assertRedirects(response, f"/admin/intake/importbatch/{batch.pk}/mapping/", fetch_redirect_response=False)
        page = self.client.get(f"/admin/intake/importbatch/{batch.pk}/mapping/").content.decode()
        self.assertIn("Leg Ref", page)
        mapping = {"trip_id": "Leg Ref", "rider_name": "Member", "rider_phone": "Member Ph", "pickup_date": "Svc Dt",
                   "pickup_time": "PU Tm", "pickup_address": "From", "dropoff_address": "To", "service_type": "LOS",
                   "prior_auth": "Auth #", "return_time": "Return", "template_name": "MTM export"}
        self.client.post(f"/admin/intake/importbatch/{batch.pk}/mapping/", mapping)
        self.assertTrue(MappingTemplate.objects.filter(name="MTM export").exists())
        report = self.client.get(f"/admin/intake/importbatch/{batch.pk}/change/").content.decode()
        self.assertIn("2 rows read", report)
        self.assertIn("2 need review", report)
        row = batch.rows.get(number=2)
        self.assertContains(self.client.get(f"/admin/intake/importbatch/row/{row.pk}/"), "MORGAN")
        self.assertTrue(AuditLog.objects.filter(action="viewed", resource_type="ImportRow").exists())
        original = self.client.get(f"/admin/intake/importbatch/{batch.pk}/file/")
        self.assertEqual(b"".join(original.streaming_content), BROKER_CSV.encode())

    def test_payer_set_on_upload_goes_on_the_trips(self):
        payer = Payer.objects.create(name="MTM", payer_type="broker")
        make_passenger(legal_name="Morgan Lee", phone="817-555-0199")
        batch = upload("mtm.csv", BROKER_CSV.encode(), user=self.user, payer=payer)
        map_broker(batch)
        self.client.post(f"/admin/intake/importbatch/{batch.pk}/create-ready/")
        self.assertEqual(Trip.objects.get(broker_trip_number="MX-1001").payer, payer)

    def test_needs_permission(self):
        User.objects.create_user(username="nobody", password="testpass123", is_staff=True)
        self.client.logout()
        login_with_otp(self.client, username="nobody", password="testpass123")
        self.assertEqual(self.client.get("/admin/intake/importbatch/upload/").status_code, 403)

"""TMHP electronic claims (ADR 0040): 837P out; 999, 277CA and 835 back; resubmission; the 95-day deadline."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from billing.models import Invoice, InvoiceStatus
from organization.models import Organization
from payers.models import Payer
from trips.models import Trip, TripStatus
from trips.tests import make_passenger

from . import services, x12
from .models import Claim, ClaimFile, ClaimSettings, ClaimStatus

CENTRAL = ZoneInfo("America/Chicago")


def envelope(st: str, body: str) -> str:
    isa = ("ISA*00*          *00*          *ZZ*TMHP           *ZZ*ANGELCARE      *270105*1200*^*00501*000000777*0*T*:")
    return f"{isa}~GS*HN*TMHP*ANGELCARE*20270105*1200*7*X*005010~ST*{st}*0001~{body}~SE*9*0001~GE*1*7~IEA*1*000000777~"


class ClaimsTestCase(TestCase):
    def setUp(self):
        org = Organization.objects.first() or Organization(phone="817-555-0100", email="office@example.com",
                                                             service_area="State of Texas")
        org.legal_name, org.ein = "AngelCare Transit LLC", "93-312-2590"
        org.save()
        self.hhsc = Payer.objects.create(name="Texas Medicaid (TMHP)", payer_type="medicaid_mco")
        config = ClaimSettings.load()
        for name, value in {
            "tmhp_payer": self.hhsc, "submitter_id": "SUB123456", "submitter_contact_name": "Billing Office",
            "submitter_contact_phone": "817-555-0100", "receiver_name": "TMHP", "receiver_id": "RECV00001",
            "payer_id": "PAYER01", "billing_npi": "1234567893", "tpi": "TPI0000001", "taxonomy_code": "343900000X",
            "billing_street": "100 Main St", "billing_city": "Fort Worth", "billing_state": "TX",
            "billing_zip": "761041234", "place_of_service": "99", "default_diagnosis_code": "Z9911",
            "default_modifier_1": "RH",
        }.items():
            setattr(config, name, value)
        config.save()
        self.config = config
        self.passenger = make_passenger(legal_name="Morgan Lee")
        for name, value in {"medicaid_id": "123456789", "date_of_birth": date(1950, 4, 2), "sex": "F",
                            "home_street": "12 Elm St", "home_city": "Fort Worth", "home_state": "TX",
                            "home_zip": "76104"}.items():
            setattr(self.passenger, name, value)
        self.passenger.save()
        self.day = services.today() - timedelta(days=10)

    def invoice(self, **extra):
        trip = Trip.objects.create(
            passenger=self.passenger, source="hhsc", payer=self.hhsc, service_type="wheelchair",
            pickup_address="12 Elm St, Fort Worth, TX 76104", dropoff_address="2 Clinic Rd, Fort Worth, TX 76104",
            scheduled_pickup_datetime=datetime.combine(self.day, time(10), tzinfo=CENTRAL),
            status=TripStatus.COMPLETED, prior_authorization_number=extra.pop("prior_auth", "PA-777"),
        )
        return Invoice.objects.create(
            trip=trip, payer=self.hhsc, amount=Decimal("63.50"), status=InvoiceStatus.SENT,
            lines=[{"label": "Wheelchair trip", "quantity": "1", "unit": "45.00", "amount": "45.00"},
                   {"label": "Miles beyond 5.00 included", "quantity": "7.4", "unit": "2.50", "amount": "18.50"}],
        )

    def claim(self, **extra):
        self.invoice(**extra)
        return services.claims_from_invoices(user=None)[-1]


class Build837PTests(ClaimsTestCase):
    def test_a_well_formed_837p(self):
        claim = self.claim()
        self.assertEqual((claim.status, claim.service_date, claim.diagnosis_code, claim.modifier_1),
                         (ClaimStatus.DRAFT, self.day, "Z9911", "RH"))
        claim_file = services.make_file([claim], user=None)
        text = claim_file.content
        self.assertEqual(text[105], "~")  # ISA is fixed-width: 106 characters with its terminator
        segs = [s.split("*") for s in text.strip("~").split("~")]
        ids = [s[0] for s in segs]
        self.assertEqual(ids[:3], ["ISA", "GS", "ST"])
        self.assertEqual(ids[-3:], ["SE", "GE", "IEA"])
        se = segs[-3]
        self.assertEqual(int(se[1]), ids.index("SE") - ids.index("ST") + 1)
        self.assertEqual(segs[0][15], "T")  # test file until settings say production
        self.assertIn(f"CLM*{claim.patient_control_number}*63.5***99:B:1*Y*A*Y*Y", text)
        self.assertIn("NM1*IL*1*LEE*MORGAN****MI*123456789", text)
        self.assertIn("DMG*D8*19500402*F", text)
        self.assertIn("REF*G1*PA-777", text)
        self.assertIn("REF*G2*TPI0000001", text)
        self.assertIn("NM1*85*2*ANGELCARE TRANSIT LLC*****XX*1234567893", text)
        self.assertIn("REF*EI*933122590", text)
        self.assertIn("HI*ABK:Z9911", text)
        self.assertIn("SV1*HC:T2003:RH*63.5*UN*1***1", text)
        self.assertIn(f"DTP*472*D8*{self.day:%Y%m%d}", text)
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.claim_file), (ClaimStatus.SUBMITTED, claim_file))

    def test_mileage_on_its_own_line_when_a_code_is_set(self):
        self.config.mileage_code = "S0215"
        self.config.save()
        text = services.make_file([self.claim()], user=None).content
        self.assertIn("SV1*HC:T2003:RH*45*UN*1***1", text)
        self.assertIn("SV1*HC:S0215*18.5*UN*7.4***1", text)

    def test_incomplete_settings_or_rider_details_make_no_file(self):
        self.config.tpi = ""
        self.config.save()
        claim = self.claim()
        with self.assertRaisesMessage(x12.X12Error, "TPI"):
            services.make_file([claim], user=None)
        self.config.tpi = "TPI0000001"
        self.config.save()
        self.passenger.medicaid_id = ""
        self.passenger.save()
        with self.assertRaisesMessage(x12.X12Error, "Medicaid ID"):
            services.make_file([claim], user=None)
        self.assertFalse(ClaimFile.objects.exists())
        claim.refresh_from_db()
        self.assertEqual(claim.status, ClaimStatus.DRAFT)

    def test_no_prior_auth_no_claim_file(self):
        with self.assertRaisesMessage(x12.X12Error, "prior authorization"):
            services.make_file([self.claim(prior_auth="")], user=None)

    def test_past_the_95_day_deadline_is_refused(self):
        claim = self.claim()
        claim.service_date = services.today() - timedelta(days=96)
        claim.save()
        self.assertEqual(claim.filing_deadline, claim.service_date + timedelta(days=95))
        with self.assertRaisesMessage(x12.X12Error, "95-day"):
            services.make_file([claim], user=None)

    def test_one_claim_per_invoice(self):
        self.claim()
        self.assertEqual(services.claims_from_invoices(user=None), [])


class ResponseTests(ClaimsTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.claim()
        self.file = services.make_file([self.c], user=None)

    def test_999_rejecting_the_file_rejects_its_claims(self):
        services.apply_response(envelope("999", f"AK1*HC*{self.file.control_number}~AK9*R*1*1*0"), "ack.999", user=None)
        self.file.refresh_from_db()
        self.c.refresh_from_db()
        self.assertEqual((self.file.acknowledged, self.c.status), ("rejected", ClaimStatus.REJECTED))

    def test_277ca_accepts_or_rejects_each_claim(self):
        pcn = self.c.patient_control_number
        services.apply_response(envelope("277", f"TRN*2*{pcn}~STC*A3:21*20270105*U*63.5~REF*1K*ICN0001"), "x.277",
                                user=None)
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, ClaimStatus.REJECTED)
        self.assertIn("277CA A3:21", self.c.response_details[0])

        other = self.claim()
        other.status = ClaimStatus.SUBMITTED
        other.save()
        services.apply_response(envelope("277", f"TRN*2*{other.patient_control_number}~STC*A1:20*20270105*WQ*63.5"
                                                f"~REF*1K*ICN0002"), "y.277", user=None)
        other.refresh_from_db()
        self.assertEqual((other.status, other.claim_number), (ClaimStatus.ACCEPTED, "ICN0002"))

    def test_835_payment_marks_the_claim_and_invoice_paid(self):
        pcn = self.c.patient_control_number
        response = services.apply_response(envelope("835", f"CLP*{pcn}*1*63.5*52.1**MC*ICN0003~CAS*CO*45*11.4"),
                                           "era.835", user=None)
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.amount_paid, self.c.claim_number),
                         (ClaimStatus.PAID, Decimal("52.10"), "ICN0003"))
        self.assertEqual(self.c.invoice.status, InvoiceStatus.PAID)
        self.assertIn("paid $52.1", response.summary[0])

    def test_835_denial_records_the_reason_and_resubmits_as_a_replacement(self):
        pcn = self.c.patient_control_number
        services.apply_response(envelope("835", f"CLP*{pcn}*4*63.5*0**MC*ICN0004~CAS*CO*197*63.5"), "era.835", user=None)
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.response_details), (ClaimStatus.DENIED, ["CO-197 $63.5"]))
        self.assertIn(self.c, [row["claim"] for row in services.needs_attention()])

        again = services.resubmit(self.c, user=None)
        self.assertEqual((again.status, again.replaces), (ClaimStatus.DRAFT, self.c))
        text = services.make_file([again], user=None).content
        self.assertIn(":B:7*", text)  # replacement claim
        self.assertIn("REF*F8*ICN0004", text)
        self.assertNotIn(self.c, [row["claim"] for row in services.needs_attention()])
        with self.assertRaises(x12.X12Error):
            services.resubmit(again, user=None)

    def test_claims_that_are_not_ours_are_listed_not_guessed(self):
        response = services.apply_response(envelope("835", "CLP*NOTOURS*1*10*10**MC*X"), "era.835", user=None)
        self.assertIn("not one of our claims", response.summary[0])

    def test_not_an_x12_file(self):
        with self.assertRaises(x12.X12Error):
            services.apply_response("hello", "notes.txt", user=None)


class AdminTests(ClaimsTestCase):
    def setUp(self):
        super().setUp()
        user = User.objects.create_user(username="admin1", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")

    def test_create_make_download_and_upload_through_the_admin(self):
        self.invoice()
        self.client.post("/admin/claims/claim/from-invoices/")
        claim = Claim.objects.get()
        response = self.client.post("/admin/claims/claim/", {"action": "make_837p", "_selected_action": [str(claim.pk)]},
                                    follow=True)
        self.assertContains(response, "Download it")
        claim_file = ClaimFile.objects.get()
        download = self.client.get(f"/admin/claims/claimfile/{claim_file.pk}/download/")
        self.assertEqual(download.content.decode(), claim_file.content)
        body = envelope("835", f"CLP*{claim.patient_control_number}*1*63.5*63.5**MC*ICN9")
        response = self.client.post("/admin/claims/responsefile/add/",
                                    {"file": SimpleUploadedFile("era.835", body.encode())}, follow=True)
        self.assertContains(response, "paid $63.5")
        self.assertContains(self.client.get("/admin/claims/claim/attention/"), "Nothing needs attention")

    def test_settings_page_says_what_is_still_needed(self):
        self.config.billing_npi = ""
        self.config.save()
        page = self.client.get("/admin/claims/claimsettings/", follow=True)
        self.assertContains(page, "billing NPI")

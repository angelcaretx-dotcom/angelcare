from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from billing.models import Invoice
from drivers.models import Driver
from passengers.models import Passenger
from payers.models import Payer
from transportation.models import TripRequest
from trips.models import Trip
from vehicles.models import Vehicle

from .models import Claim, ClaimStatus


def make_payer(**overrides) -> Payer:
    defaults = {"name": "Sample Medicaid MCO", "payer_type": "medicaid_mco"}
    defaults.update(overrides)
    return Payer.objects.create(**defaults)


def make_invoice(**overrides) -> Invoice:
    passenger = Passenger.objects.create(legal_name="Morgan Lee", phone="817-555-0199")
    trip_request = TripRequest.objects.create(
        full_name="Morgan Lee",
        phone="817-555-0199",
        email="morgan@example.com",
        pickup_address="1 Main St",
        dropoff_address="2 Clinic Rd",
        requested_datetime=timezone.now() + timedelta(days=2),
        service_type="ambulatory",
        passenger=passenger,
    )
    driver = Driver.objects.create(
        legal_name="Sam Taylor",
        phone="817-555-0155",
        employment_type="employee",
        license_number="TX-DL-123456",
        license_expiration_date=date.today() + timedelta(days=365),
        status="active",
    )
    vehicle = Vehicle.objects.create(
        vin="1HGCM82633A123456",
        make="Ford",
        model="Transit",
        year=2022,
        license_plate="TX-ABC123",
        passenger_capacity=4,
        registration_expiration_date=date.today() + timedelta(days=180),
        inspection_expiration_date=date.today() + timedelta(days=90),
        status="active",
    )
    trip = Trip.objects.create(
        trip_request=trip_request,
        driver=driver,
        vehicle=vehicle,
        scheduled_pickup_datetime=timezone.now() + timedelta(days=2),
    )
    defaults = {"trip": trip, "amount": Decimal("125.00")}
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


def make_claim(**overrides) -> Claim:
    # Deliberately not a plain dict-literal-then-update -- that would
    # eagerly call make_invoice()/make_payer() (each creates a real
    # row chain, including a hardcoded-VIN Vehicle) even when the
    # caller overrides them, which is wasteful and -- when a test
    # calls make_claim() more than once with an explicit invoice=
    # override, as test_multiple_claims_can_exist_for_one_invoice does
    # -- collides on Vehicle.vin's unique constraint.
    if "invoice" not in overrides:
        overrides["invoice"] = make_invoice()
    if "payer" not in overrides:
        overrides["payer"] = make_payer()
    overrides.setdefault("amount_claimed", Decimal("125.00"))
    return Claim.objects.create(**overrides)


class ClaimModelTests(TestCase):
    def test_default_status_is_submitted(self):
        claim = make_claim()
        self.assertEqual(claim.status, ClaimStatus.SUBMITTED)

    def test_payer_is_required(self):
        claim = Claim(invoice=make_invoice(), amount_claimed=Decimal("100.00"))
        with self.assertRaises(ValidationError):
            claim.full_clean()

    def test_negative_amount_claimed_is_rejected(self):
        claim = Claim(
            invoice=make_invoice(), payer=make_payer(), amount_claimed=Decimal("-1.00")
        )
        with self.assertRaises(ValidationError):
            claim.full_clean()

    def test_multiple_claims_can_exist_for_one_invoice(self):
        invoice = make_invoice()
        payer = make_payer()
        first = make_claim(invoice=invoice, payer=payer, status=ClaimStatus.DENIED)
        second = make_claim(invoice=invoice, payer=payer)
        self.assertEqual(invoice.claims.count(), 2)
        self.assertNotEqual(first.id, second.id)

    def test_str_includes_amount_and_status(self):
        claim = make_claim(amount_claimed=Decimal("75.00"))
        self.assertIn("75.00", str(claim))
        self.assertIn("submitted", str(claim))


class ClaimAdminAccessTests(TestCase):
    def setUp(self):
        self.claim = make_claim()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_cannot_view_claims(self):
        # Same reasoning as Billing (ADR 0018): financial, not dispatch
        # work -- no confirmed need for Dispatcher access.
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher1", password="testpass123")

        response = self.client.get("/admin/claims/claim/")
        self.assertEqual(response.status_code, 403)

    def test_administrator_can_view_and_change_claims(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        login_with_otp(self.client, username="admin1", password="testpass123")

        list_response = self.client.get("/admin/claims/claim/")
        self.assertEqual(list_response.status_code, 200)

        change_response = self.client.get(f"/admin/claims/claim/{self.claim.id}/change/")
        self.assertEqual(change_response.status_code, 200)


class ClaimStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.claim = make_claim()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/claims/claim/{self.claim.id}/change/"
        post_data = {
            "invoice": str(self.claim.invoice_id),
            "payer": str(self.claim.payer_id),
            "claim_number": "",
            "amount_claimed": "125.00",
            "amount_paid": "",
            "status": "accepted",
            "submitted_date": "",
            "response_date": date.today().isoformat(),
            "notes": "",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Claim", resource_id=str(self.claim.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "submitted"})
        self.assertEqual(logs.first().after, {"status": "accepted"})

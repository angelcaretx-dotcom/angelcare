from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from drivers.models import Driver
from passengers.models import Passenger
from payers.models import Payer
from transportation.models import TripRequest
from trips.models import Trip
from vehicles.models import Vehicle

from .models import Invoice, InvoiceStatus


def make_trip(**overrides) -> Trip:
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
    defaults = {
        "trip_request": trip_request,
        "driver": driver,
        "vehicle": vehicle,
        "scheduled_pickup_datetime": timezone.now() + timedelta(days=2),
    }
    defaults.update(overrides)
    return Trip.objects.create(**defaults)


def make_invoice(**overrides) -> Invoice:
    defaults = {"trip": make_trip(), "amount": Decimal("125.00")}
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


class InvoiceModelTests(TestCase):
    def test_default_status_is_draft(self):
        invoice = make_invoice()
        self.assertEqual(invoice.status, InvoiceStatus.DRAFT)

    def test_payer_is_optional(self):
        invoice = make_invoice(payer=None)
        self.assertIsNone(invoice.payer)

    def test_negative_amount_is_rejected(self):
        invoice = Invoice(trip=make_trip(), amount=Decimal("-1.00"))
        with self.assertRaises(ValidationError):
            invoice.full_clean()

    def test_str_includes_amount_and_status(self):
        invoice = make_invoice(amount=Decimal("50.00"))
        self.assertIn("50.00", str(invoice))
        self.assertIn("draft", str(invoice))


class InvoiceAdminAccessTests(TestCase):
    def setUp(self):
        self.invoice = make_invoice()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_cannot_view_invoices(self):
        # Billing is administrative/financial, not dispatch work -- no
        # confirmed need for Dispatcher access (least privilege, same
        # reasoning ADR 0007 applied to Driver/Vehicle edit access).
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        login_with_otp(self.client, username="dispatcher1", password="testpass123")

        response = self.client.get("/admin/billing/invoice/")
        self.assertEqual(response.status_code, 403)

    def test_administrator_can_view_and_change_invoices(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        login_with_otp(self.client, username="admin1", password="testpass123")

        list_response = self.client.get("/admin/billing/invoice/")
        self.assertEqual(list_response.status_code, 200)

        change_response = self.client.get(f"/admin/billing/invoice/{self.invoice.id}/change/")
        self.assertEqual(change_response.status_code, 200)


class InvoiceStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")
        self.invoice = make_invoice()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/billing/invoice/{self.invoice.id}/change/"
        post_data = {
            "trip": str(self.invoice.trip_id),
            "amount": "125.00",
            "status": "sent",
            "issued_date": date.today().isoformat(),
            "paid_date": "",
            "notes": "",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Invoice", resource_id=str(self.invoice.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "draft"})
        self.assertEqual(logs.first().after, {"status": "sent"})

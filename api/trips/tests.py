from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditLog
from drivers.models import Driver
from passengers.models import Passenger
from transportation.models import TripRequest, TripRequestStatus
from vehicles.models import Vehicle

from .models import Trip, TripStatus


def make_passenger(**overrides) -> Passenger:
    defaults = {"legal_name": "Morgan Lee", "phone": "817-555-0199"}
    defaults.update(overrides)
    return Passenger.objects.create(**defaults)


def make_trip_request(*, passenger=None, **overrides) -> TripRequest:
    defaults = {
        "full_name": "Morgan Lee",
        "phone": "817-555-0199",
        "email": "morgan@example.com",
        "pickup_address": "1 Main St",
        "dropoff_address": "2 Clinic Rd",
        "requested_datetime": timezone.now() + timedelta(days=2),
        "service_type": "ambulatory",
        "passenger": passenger,
    }
    defaults.update(overrides)
    return TripRequest.objects.create(**defaults)


def make_driver(**overrides) -> Driver:
    defaults = {
        "legal_name": "Sam Taylor",
        "phone": "817-555-0155",
        "employment_type": "employee",
        "license_number": "TX-DL-123456",
        "license_expiration_date": date.today() + timedelta(days=365),
        "status": "active",
    }
    defaults.update(overrides)
    return Driver.objects.create(**defaults)


def make_vehicle(**overrides) -> Vehicle:
    defaults = {
        "vin": "1HGCM82633A123456",
        "make": "Ford",
        "model": "Transit",
        "year": 2022,
        "license_plate": "TX-ABC123",
        "passenger_capacity": 4,
        "registration_expiration_date": date.today() + timedelta(days=180),
        "inspection_expiration_date": date.today() + timedelta(days=90),
        "status": "active",
    }
    defaults.update(overrides)
    return Vehicle.objects.create(**defaults)


class TripModelValidationTests(TestCase):
    def setUp(self):
        self.passenger = make_passenger()
        self.trip_request = make_trip_request(passenger=self.passenger)
        self.driver = make_driver()
        self.vehicle = make_vehicle()

    def make_trip(self, **overrides):
        defaults = {
            "trip_request": self.trip_request,
            "driver": self.driver,
            "vehicle": self.vehicle,
            "scheduled_pickup_datetime": timezone.now() + timedelta(days=2),
        }
        defaults.update(overrides)
        return Trip(**defaults)

    def test_valid_trip_passes_validation(self):
        trip = self.make_trip()
        trip.full_clean()  # should not raise

    def test_cannot_schedule_without_linked_passenger(self):
        unlinked_request = make_trip_request(passenger=None, email="other@example.com")
        trip = self.make_trip(trip_request=unlinked_request)

        with self.assertRaises(ValidationError) as ctx:
            trip.full_clean()
        self.assertIn("trip_request", ctx.exception.message_dict)

    def test_cannot_assign_driver_with_expired_license(self):
        expired_driver = make_driver(
            license_expiration_date=date.today() - timedelta(days=1),
            license_number="TX-DL-EXPIRED",
        )
        trip = self.make_trip(driver=expired_driver)

        with self.assertRaises(ValidationError) as ctx:
            trip.full_clean()
        self.assertIn("driver", ctx.exception.message_dict)

    def test_cannot_assign_suspended_driver(self):
        suspended_driver = make_driver(status="suspended", license_number="TX-DL-SUSP")
        trip = self.make_trip(driver=suspended_driver)

        with self.assertRaises(ValidationError) as ctx:
            trip.full_clean()
        self.assertIn("driver", ctx.exception.message_dict)

    def test_cannot_assign_vehicle_with_expired_registration(self):
        expired_vehicle = make_vehicle(
            registration_expiration_date=date.today() - timedelta(days=1),
            vin="1HGCM82633A999999",
        )
        trip = self.make_trip(vehicle=expired_vehicle)

        with self.assertRaises(ValidationError) as ctx:
            trip.full_clean()
        self.assertIn("vehicle", ctx.exception.message_dict)

    def test_cannot_assign_vehicle_with_expired_inspection(self):
        expired_vehicle = make_vehicle(
            inspection_expiration_date=date.today() - timedelta(days=1),
            vin="1HGCM82633A888888",
        )
        trip = self.make_trip(vehicle=expired_vehicle)

        with self.assertRaises(ValidationError) as ctx:
            trip.full_clean()
        self.assertIn("vehicle", ctx.exception.message_dict)

    def test_default_status_is_scheduled(self):
        trip = self.make_trip()
        trip.full_clean()
        trip.save()
        self.assertEqual(trip.status, TripStatus.SCHEDULED)


class TripAdminIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dispatcher1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Dispatcher"))
        self.client.login(username="dispatcher1", password="testpass123")

        self.passenger = make_passenger()
        self.trip_request = make_trip_request(passenger=self.passenger)
        self.driver = make_driver()
        self.vehicle = make_vehicle()

    def test_scheduling_a_trip_updates_trip_request_status(self):
        self.assertEqual(self.trip_request.status, TripRequestStatus.NEW)

        response = self.client.post(
            "/admin/trips/trip/add/",
            {
                "trip_request": str(self.trip_request.id),
                "driver": str(self.driver.id),
                "vehicle": str(self.vehicle.id),
                "scheduled_pickup_datetime_0": "2027-01-01",
                "scheduled_pickup_datetime_1": "10:00:00",
                "status": "scheduled",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.trip_request.refresh_from_db()
        self.assertEqual(self.trip_request.status, TripRequestStatus.SCHEDULED)

        request_logs = AuditLog.objects.filter(
            resource_type="TripRequest", resource_id=str(self.trip_request.id)
        )
        self.assertEqual(request_logs.count(), 1)
        self.assertEqual(request_logs.first().after, {"status": "scheduled"})

    def test_admin_rejects_expired_driver_license(self):
        expired_driver = make_driver(
            license_expiration_date=date.today() - timedelta(days=1),
            license_number="TX-DL-EXPIRED",
        )

        response = self.client.post(
            "/admin/trips/trip/add/",
            {
                "trip_request": str(self.trip_request.id),
                "driver": str(expired_driver.id),
                "vehicle": str(self.vehicle.id),
                "scheduled_pickup_datetime_0": "2027-01-01",
                "scheduled_pickup_datetime_1": "10:00:00",
                "status": "scheduled",
                "notes": "",
            },
        )
        # Re-renders the form with an error, not a redirect -- and no
        # Trip was created.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Trip.objects.count(), 0)

    def test_status_change_creates_audit_entry(self):
        trip = Trip.objects.create(
            trip_request=self.trip_request,
            driver=self.driver,
            vehicle=self.vehicle,
            scheduled_pickup_datetime=timezone.now() + timedelta(days=2),
        )

        response = self.client.post(
            f"/admin/trips/trip/{trip.id}/change/",
            {
                "trip_request": str(self.trip_request.id),
                "driver": str(self.driver.id),
                "vehicle": str(self.vehicle.id),
                "scheduled_pickup_datetime_0": "2027-01-01",
                "scheduled_pickup_datetime_1": "10:00:00",
                "status": "completed",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Trip", resource_id=str(trip.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "scheduled"})
        self.assertEqual(logs.first().after, {"status": "completed"})

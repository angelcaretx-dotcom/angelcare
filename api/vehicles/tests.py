from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase

from audit.models import AuditLog

from .models import Vehicle, VehicleStatus


def make_vehicle(**overrides) -> Vehicle:
    defaults = {
        "vin": "1HGCM82633A123456",
        "make": "Ford",
        "model": "Transit",
        "year": 2022,
        "license_plate": "TX-ABC123",
        "wheelchair_capable": True,
        "passenger_capacity": 4,
        "registration_expiration_date": date.today() + timedelta(days=180),
        "inspection_expiration_date": date.today() + timedelta(days=90),
    }
    defaults.update(overrides)
    return Vehicle.objects.create(**defaults)


class VehicleModelTests(TestCase):
    def test_default_status_is_active(self):
        vehicle = make_vehicle()
        self.assertEqual(vehicle.status, VehicleStatus.ACTIVE)

    def test_str_representation(self):
        vehicle = make_vehicle(make="Ford", model="Transit", year=2022)
        self.assertEqual(str(vehicle), "2022 Ford Transit (TX-ABC123)")

    def test_vin_must_be_unique(self):
        make_vehicle(vin="1HGCM82633A123456")
        with self.assertRaises(Exception):
            make_vehicle(vin="1HGCM82633A123456", license_plate="TX-XYZ789")


class VehicleAdminAccessTests(TestCase):
    def setUp(self):
        self.vehicle = make_vehicle()

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_but_not_change_vehicles(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        self.client.login(username="dispatcher1", password="testpass123")

        list_response = self.client.get("/admin/vehicles/vehicle/")
        self.assertEqual(list_response.status_code, 200)

        # View-only permission renders the change page read-only (200),
        # not a 403 -- Django only blocks the actual save attempt.
        change_url = f"/admin/vehicles/vehicle/{self.vehicle.id}/change/"
        get_response = self.client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(change_url, {"make": "Changed"})
        self.assertEqual(post_response.status_code, 403)
        self.vehicle.refresh_from_db()
        self.assertNotEqual(self.vehicle.make, "Changed")

    def test_administrator_can_change_vehicles(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        self.client.login(username="admin1", password="testpass123")

        response = self.client.get(f"/admin/vehicles/vehicle/{self.vehicle.id}/change/")
        self.assertEqual(response.status_code, 200)


class VehicleStatusChangeAuditIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.user.groups.add(Group.objects.get(name="Administrator"))
        self.client.login(username="admin1", password="testpass123")
        self.vehicle = make_vehicle()

    def test_changing_status_via_admin_creates_audit_entry(self):
        change_url = f"/admin/vehicles/vehicle/{self.vehicle.id}/change/"
        post_data = {
            "vin": self.vehicle.vin,
            "make": self.vehicle.make,
            "model": self.vehicle.model,
            "year": self.vehicle.year,
            "license_plate": self.vehicle.license_plate,
            "wheelchair_capable": "on",
            "passenger_capacity": self.vehicle.passenger_capacity,
            "registration_expiration_date": self.vehicle.registration_expiration_date.isoformat(),
            "inspection_expiration_date": self.vehicle.inspection_expiration_date.isoformat(),
            "notes": "",
            "status": "maintenance",
            "documents-document-content_type-object_id-TOTAL_FORMS": "0",
            "documents-document-content_type-object_id-INITIAL_FORMS": "0",
            "documents-document-content_type-object_id-MIN_NUM_FORMS": "0",
            "documents-document-content_type-object_id-MAX_NUM_FORMS": "1000",
        }
        response = self.client.post(change_url, post_data)
        self.assertEqual(response.status_code, 302)

        logs = AuditLog.objects.filter(resource_type="Vehicle", resource_id=str(self.vehicle.id))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().before, {"status": "active"})
        self.assertEqual(logs.first().after, {"status": "maintenance"})

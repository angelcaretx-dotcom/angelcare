from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from transportation.models import TripRequest


class RoleSeedingTests(TestCase):
    """
    Runs against the test database, which is migrated fresh (all apps,
    one pass) on every test run -- the exact scenario that originally
    exposed a bug where permissions for models introduced in the same
    migration run weren't available yet to the role-seeding migration.
    """

    def test_dispatcher_group_exists_with_expected_permissions(self):
        dispatcher = Group.objects.get(name="Dispatcher")
        codenames = set(dispatcher.permissions.values_list("codename", flat=True))
        self.assertEqual(
            codenames,
            {
                "view_triprequest",
                "change_triprequest",
                "view_passenger",
                "change_passenger",
                "add_passenger",
                "view_driver",
                "view_vehicle",
            },
        )

    def test_administrator_group_exists_with_expected_permissions(self):
        administrator = Group.objects.get(name="Administrator")
        codenames = set(administrator.permissions.values_list("codename", flat=True))
        self.assertEqual(
            codenames,
            {
                "view_triprequest",
                "change_triprequest",
                "add_triprequest",
                "delete_triprequest",
                "view_passenger",
                "change_passenger",
                "add_passenger",
                "delete_passenger",
                "view_driver",
                "change_driver",
                "add_driver",
                "delete_driver",
                "view_vehicle",
                "change_vehicle",
                "add_vehicle",
                "delete_vehicle",
                "view_notificationlog",
                "view_auditlog",
            },
        )

    def test_dispatcher_group_cannot_delete_trip_requests(self):
        dispatcher = Group.objects.get(name="Dispatcher")
        delete_perm = Permission.objects.get(codename="delete_triprequest")
        self.assertNotIn(delete_perm, dispatcher.permissions.all())


class RoleEnforcementTests(TestCase):
    """
    Proves the roles actually restrict access in the admin, not just
    that the Group rows exist.
    """

    def setUp(self):
        self.trip_request = TripRequest.objects.create(
            full_name="Test Passenger",
            phone="817-555-0100",
            email="passenger@example.com",
            pickup_address="1 Main St",
            dropoff_address="2 Clinic Rd",
            requested_datetime="2027-01-01T10:00:00Z",
            service_type="ambulatory",
        )

    def make_staff_user(self, *, username, groups):
        user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
        for group_name in groups:
            user.groups.add(Group.objects.get(name=group_name))
        return user

    def test_dispatcher_can_view_trip_requests_in_admin(self):
        self.make_staff_user(username="dispatcher1", groups=["Dispatcher"])
        self.client.login(username="dispatcher1", password="testpass123")

        response = self.client.get("/admin/transportation/triprequest/")
        self.assertEqual(response.status_code, 200)

    def test_dispatcher_cannot_delete_trip_requests(self):
        self.make_staff_user(username="dispatcher2", groups=["Dispatcher"])
        self.client.login(username="dispatcher2", password="testpass123")

        response = self.client.get(
            f"/admin/transportation/triprequest/{self.trip_request.id}/delete/"
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_user_with_no_group_cannot_view_trip_requests(self):
        User.objects.create_user(username="norole", password="testpass123", is_staff=True)
        self.client.login(username="norole", password="testpass123")

        response = self.client.get("/admin/transportation/triprequest/")
        self.assertEqual(response.status_code, 403)

    def test_administrator_can_delete_trip_requests(self):
        self.make_staff_user(username="admin1", groups=["Administrator"])
        self.client.login(username="admin1", password="testpass123")

        response = self.client.post(
            f"/admin/transportation/triprequest/{self.trip_request.id}/delete/",
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TripRequest.objects.filter(id=self.trip_request.id).exists())

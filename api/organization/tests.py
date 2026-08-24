from django.contrib.auth.models import Group, User
from django.test import TestCase

from .models import Organization


class OrganizationSeedMigrationTests(TestCase):
    """
    Runs against the test database, which applies every migration
    (including the data migration) fresh on every test run.
    """

    def test_seed_migration_created_exactly_one_record(self):
        self.assertEqual(Organization.objects.count(), 1)

    def test_seeded_record_matches_confirmed_business_facts(self):
        org = Organization.objects.first()
        self.assertEqual(org.legal_name, "AngelCare Transit")
        self.assertEqual(org.phone, "817-766-9228")
        self.assertEqual(org.email, "angelcaretx@gmail.com")
        self.assertEqual(org.service_area, "State of Texas")


class OrganizationAdminSingletonTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="super1", password="testpass123", email="super1@example.com"
        )
        self.client.login(username="super1", password="testpass123")

    def test_cannot_add_a_second_organization(self):
        # The seed migration already created one.
        response = self.client.get("/admin/organization/organization/add/")
        self.assertEqual(response.status_code, 403)

    def test_cannot_delete_the_organization(self):
        org = Organization.objects.first()
        response = self.client.get(f"/admin/organization/organization/{org.id}/delete/")
        self.assertEqual(response.status_code, 403)

    def test_can_view_and_change_the_organization(self):
        org = Organization.objects.first()
        response = self.client.get(f"/admin/organization/organization/{org.id}/change/")
        self.assertEqual(response.status_code, 200)

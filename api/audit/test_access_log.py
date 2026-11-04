from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from accounts.otp_test_utils import login_with_otp
from notifications.tests import make_trip_request

from .models import AuditLog


class AccessLogTests(TestCase):
    """ADR 0027: opening a rider's record leaves a trail, not just editing it."""

    def setUp(self):
        user = User.objects.create_user(username="dana", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Dispatcher"))
        login_with_otp(self.client, username="dana", password="testpass123")
        self.user = user
        self.trip_request = make_trip_request()

    def views(self, **filters):
        return AuditLog.objects.filter(actor=self.user, **filters)

    def test_opening_a_record_is_logged(self):
        url = reverse("admin:transportation_triprequest_change", args=[self.trip_request.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

        entry = self.views(action="viewed", resource_type="TripRequest").get()
        self.assertEqual(entry.resource_id, str(self.trip_request.pk))
        self.assertEqual(entry.source, "admin")

    def test_list_page_is_logged_with_the_search_used(self):
        url = reverse("admin:transportation_triprequest_changelist")
        self.client.get(url, {"q": "Rivera"})

        entry = self.views(action="viewed_list", resource_type="TripRequest").get()
        self.assertEqual(entry.after, {"query": "q=Rivera"})

    def test_saving_is_not_double_logged_as_a_view(self):
        url = reverse("admin:transportation_triprequest_change", args=[self.trip_request.pk])
        self.client.post(url, {})  # invalid form -> re-rendered, but not a GET
        self.assertFalse(self.views(action="viewed").exists())

    def test_missing_record_is_not_logged(self):
        url = reverse(
            "admin:transportation_triprequest_change",
            args=["00000000-0000-0000-0000-000000000000"],
        )
        self.client.get(url)
        self.assertFalse(self.views(action="viewed").exists())

    def test_dashboard_view_is_logged(self):
        self.client.get(reverse("admin:index"))
        self.assertTrue(self.views(action="viewed", resource_type="Dashboard").exists())

    def test_every_phi_admin_logs_views(self):
        from django.contrib import admin

        from .admin_mixins import AccessLoggedAdminMixin

        logged = {
            model._meta.label
            for model, model_admin in admin.site._registry.items()
            if isinstance(model_admin, AccessLoggedAdminMixin)
        }
        self.assertTrue(
            {
                "transportation.TripRequest",
                "passengers.Passenger",
                "trips.Trip",
                "billing.Invoice",
                "claims.Claim",
                "notifications.NotificationLog",
            }
            <= logged
        )

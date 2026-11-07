from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path
from django.views.decorators.cache import never_cache

from accounts.admin_base import AngelCareModelAdmin

from . import launch
from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(AngelCareModelAdmin):
    list_display = ("legal_name", "phone", "email", "service_area", "ein", "updated_at")
    readonly_fields = ("id", "created_at", "updated_at")

    def has_add_permission(self, request):
        # Practical singleton: one Organization record for now (see
        # models.py docstring for why this isn't a hard DB constraint).
        if Organization.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # The organization record should never simply disappear.
        return False

    def get_urls(self):
        return [path("launch/", self.admin_site.admin_view(never_cache(self.launch_view)),
                     name="organization_launch_checklist")] + super().get_urls()

    def launch_view(self, request):
        """The launch checklist (ADR 0045): superusers, since it spans every area."""
        if not request.user.is_superuser:
            raise PermissionDenied
        from trips.standing import texas_today

        items = launch.checklist(request.user, texas_today())
        areas = {}
        for item in items:
            areas.setdefault(item.area, []).append(item)
        return TemplateResponse(request, "admin/organization/launch.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "title": "Launch checklist",
            "areas": areas.items(), "todo": sum(i.state == launch.TODO for i in items),
            "done": sum(i.state == launch.DONE for i in items),
            "manual": sum(i.state == launch.MANUAL for i in items),
        })

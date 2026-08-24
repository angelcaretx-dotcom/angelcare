from django.contrib import admin

from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "phone", "email", "service_area", "updated_at")
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

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "actor",
        "action",
        "resource_type",
        "resource_id",
        "source",
    )
    list_filter = ("action", "resource_type", "source")
    search_fields = ("resource_id", "actor__username")
    readonly_fields = (
        "id",
        "actor",
        "action",
        "resource_type",
        "resource_id",
        "before",
        "after",
        "source",
        "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        # Entries are created only by the audit system itself.
        return False

    def has_change_permission(self, request, obj=None):
        # Audit entries must not be editable after the fact.
        return False

    def has_delete_permission(self, request, obj=None):
        # Audit entries must not be casually deletable (Section 15).
        return False

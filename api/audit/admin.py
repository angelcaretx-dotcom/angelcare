from django.contrib import admin
from django.contrib.admin.models import LogEntry

from accounts.admin_base import AngelCareModelAdmin

from .models import AuditLog


@admin.register(LogEntry)
class AdminHistoryAdmin(AngelCareModelAdmin):
    """
    Django's own record of every add, change and delete made in the admin,
    with the record's name as it was then: it answers "what was that
    record we deleted?". Superusers only (no role is given it); read-only.
    """

    list_display = ("action_time", "user", "action_flag", "content_type", "object_repr", "change_message")
    list_filter = ("action_flag", "content_type")
    search_fields = ("object_repr", "user__username")
    ordering = ("-action_time",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(AngelCareModelAdmin):
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

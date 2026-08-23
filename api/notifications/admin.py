from django.contrib import admin

from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = (
        "notification_type",
        "recipient",
        "status",
        "related_object_type",
        "related_object_id",
        "created_at",
    )
    list_filter = ("status", "notification_type")
    search_fields = ("recipient", "related_object_id", "error_message")
    readonly_fields = (
        "id",
        "notification_type",
        "recipient",
        "status",
        "error_message",
        "related_object_type",
        "related_object_id",
        "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        # Log entries are created only by the notification system itself.
        return False

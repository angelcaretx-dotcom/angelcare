from django.contrib import admin
from django.utils import timezone

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change

from .models import ContactMessage, MessageStatus


@admin.register(ContactMessage)
class ContactMessageAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Website messages (ADR 0033). Read them here and reply from your own
    mailbox; mark them Handled when done. The text is what the sender
    wrote, so it's read-only.
    """

    list_display = ("created_at", "name", "email", "phone", "status", "handled_by")
    list_filter = ("status",)
    search_fields = ("name", "email", "phone")
    fields = ("status", "name", "email", "phone", "message", "created_at", "handled_by", "handled_at")
    readonly_fields = ("name", "email", "phone", "message", "created_at", "handled_by", "handled_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data:
            handled = obj.status == MessageStatus.HANDLED
            obj.handled_by = request.user if handled else None
            obj.handled_at = timezone.now() if handled else None
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="ContactMessage",
                resource_id=obj.id,
                before={"status": form.initial.get("status")},
                after={"status": obj.status},
                source="admin",
            )
        super().save_model(request, obj, form, change)

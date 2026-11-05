import re

from django.contrib import admin
from django.utils import timezone

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change

from .models import ContactMessage, MessageStatus

REFERENCE_RE = re.compile(r"[0-9a-f]{8}")


@admin.register(ContactMessage)
class ContactMessageAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Website messages (ADR 0033). Read them here and reply from your own
    mailbox; mark them Handled when done. The text is what the sender
    wrote, so it's read-only.
    """

    list_display = ("reference", "created_at", "name", "email", "phone", "status", "handled_by")
    list_display_links = ("reference", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "email", "phone")
    search_help_text = "Name, email, phone, or the message number from the alert email (e.g. #1A2B3C4D)."
    fields = ("reference", "status", "name", "email", "phone", "message", "created_at", "handled_by", "handled_at")
    readonly_fields = ("reference", "name", "email", "phone", "message", "created_at", "handled_by", "handled_at")
    ordering = ("-created_at",)

    @admin.display(description="Message number")
    def reference(self, obj):
        return obj.reference

    def get_search_results(self, request, queryset, search_term):
        # The alert email names a message by "#" + the start of its id.
        number = search_term.strip().lstrip("#").lower()
        if REFERENCE_RE.fullmatch(number):
            return queryset.filter(id__startswith=number), False
        return super().get_search_results(request, queryset, search_term)

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

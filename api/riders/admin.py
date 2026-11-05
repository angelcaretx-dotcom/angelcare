from django.contrib import admin, messages
from unfold.admin import TabularInline

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin

from .models import RiderAccount, SavedAddress
from .services import send_invite


class SavedAddressInline(TabularInline):
    model = SavedAddress
    extra = 0
    can_delete = False
    fields = ("label", "address", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(RiderAccount)
class RiderAccountAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Website accounts (ADR 0031). Created automatically by bookings and
    managed by their owners; staff can look, resend an unused invite,
    or switch an account off -- not create or edit them.
    """

    list_display = ("email", "full_name", "verified_at", "invited_at", "created_at")
    list_filter = (("verified_at", admin.EmptyFieldListFilter),)
    search_fields = ("email", "full_name", "phone")
    readonly_fields = (
        "email", "full_name", "phone", "mobility_notes", "invited_at", "verified_at", "created_at", "updated_at",
    )
    fields = readonly_fields
    inlines = [SavedAddressInline]
    actions = ["resend_invite"]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    @admin.action(description="Resend the set-up link (unused accounts only)")
    def resend_invite(self, request, queryset):
        pending = queryset.filter(verified_at__isnull=True)
        for account in pending:
            send_invite(account)
        messages.info(request, f"Set-up link re-sent to {pending.count()} account(s).")

from django.contrib import admin

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change

from .models import Claim


@admin.register(Claim)
class ClaimAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    list_display = (
        "id",
        "invoice",
        "payer",
        "amount_claimed",
        "amount_paid",
        "status",
        "submitted_date",
        "created_at",
    )
    list_filter = ("status", "payer")
    search_fields = ("claim_number", "invoice__trip__passenger__legal_name")
    autocomplete_fields = ("invoice", "payer")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Claim",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

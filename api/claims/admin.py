from django.contrib import admin

from audit.services import record_change

from .models import Claim


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
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
    search_fields = ("claim_number", "invoice__trip__trip_request__full_name")
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

from django.contrib import admin

from audit.services import record_change

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "trip",
        "payer",
        "amount",
        "status",
        "issued_date",
        "paid_date",
        "created_at",
    )
    list_filter = ("status", "payer")
    search_fields = ("trip__trip_request__full_name",)
    autocomplete_fields = ("trip", "payer")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Invoice",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

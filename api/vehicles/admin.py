from django.contrib import admin

from audit.services import record_change
from documents.admin import DocumentInline, DocumentUploaderAdminMixin

from .models import Vehicle


@admin.register(Vehicle)
class VehicleAdmin(DocumentUploaderAdminMixin, admin.ModelAdmin):
    list_display = (
        "__str__",
        "vin",
        "wheelchair_capable",
        "stretcher_capable",
        "passenger_capacity",
        "status",
        "created_at",
    )
    list_filter = ("status", "wheelchair_capable", "stretcher_capable")
    search_fields = ("vin", "make", "model", "license_plate")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("make", "model")
    inlines = [DocumentInline]

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Vehicle",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

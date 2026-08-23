from django.contrib import admin

from audit.services import record_change

from .models import Passenger


@admin.register(Passenger)
class PassengerAdmin(admin.ModelAdmin):
    list_display = (
        "legal_name",
        "preferred_name",
        "phone",
        "preferred_service_type",
        "status",
        "created_at",
    )
    list_filter = ("status", "preferred_service_type")
    search_fields = ("legal_name", "preferred_name", "phone", "email")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("legal_name",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Passenger",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

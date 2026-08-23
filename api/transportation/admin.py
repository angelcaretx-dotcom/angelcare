from django.contrib import admin

from audit.services import record_change

from .models import TripRequest


@admin.register(TripRequest)
class TripRequestAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone",
        "service_type",
        "requested_datetime",
        "status",
        "created_at",
    )
    list_filter = ("status", "service_type")
    search_fields = ("full_name", "phone", "email", "pickup_address", "dropoff_address")
    readonly_fields = ("id", "created_at", "updated_at", "source")
    ordering = ("-created_at",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="TripRequest",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

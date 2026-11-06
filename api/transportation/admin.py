from django.contrib import admin

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change

from .models import TripRequest


@admin.register(TripRequest)
class TripRequestAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    list_display = (
        "full_name",
        "phone",
        "service_type",
        "requested_datetime",
        "status",
        "passenger",
        "created_at",
    )
    list_filter = ("status", "service_type", "booked_for", "return_ride", "source")
    search_fields = ("full_name", "phone", "email", "pickup_address", "dropoff_address")
    readonly_fields = ("id", "created_at", "updated_at", "source", "facility", "facility_user")
    autocomplete_fields = ("passenger",)
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("status", "passenger", "source")}),
        ("Who", {"fields": ("full_name", "phone", "email", "booked_for", "facility", "facility_user")}),
        (
            "Pickup",
            {"fields": ("pickup_address", "pickup_street", "pickup_unit", "pickup_city", "pickup_state", "pickup_zip")},
        ),
        (
            "Drop-off",
            {
                "fields": (
                    "dropoff_address",
                    "dropoff_street",
                    "dropoff_unit",
                    "dropoff_city",
                    "dropoff_state",
                    "dropoff_zip",
                )
            },
        ),
        ("When", {"fields": ("requested_datetime", "return_ride", "return_datetime")}),
        ("Service", {"fields": ("service_type", "mobility_notes", "additional_notes")}),
        ("Record", {"fields": ("id", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        # With the parts on file (ADR 0032), the one-line address is
        # composed from them: edit the parts instead.
        for side in ("pickup", "dropoff"):
            if obj is not None and obj.has_address_parts(side):
                fields.append(f"{side}_address")
        return fields

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

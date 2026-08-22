from django.contrib import admin

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

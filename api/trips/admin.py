from django.contrib import admin

from audit.services import record_change
from transportation.models import TripRequestStatus

from .models import Trip


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = (
        "trip_request",
        "driver",
        "vehicle",
        "scheduled_pickup_datetime",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    autocomplete_fields = ("trip_request", "driver", "vehicle")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-scheduled_pickup_datetime",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        # Model.clean() (credential/status checks -- see trips/models.py)
        # runs automatically as part of the admin's ModelForm validation
        # before save_model is ever called, so obj is already valid here.
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Trip",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

        if not change:
            # Scheduling a trip is a real commitment of resources --
            # reflect that on the originating request too, so its own
            # status (and audit trail) stays truthful.
            trip_request = obj.trip_request
            if trip_request.status != TripRequestStatus.SCHEDULED:
                previous_request_status = trip_request.status
                trip_request.status = TripRequestStatus.SCHEDULED
                trip_request.save(update_fields=["status", "updated_at"])
                record_change(
                    actor=request.user,
                    action="status_changed",
                    resource_type="TripRequest",
                    resource_id=trip_request.id,
                    before={"status": previous_request_status},
                    after={"status": TripRequestStatus.SCHEDULED},
                    source="admin",
                )

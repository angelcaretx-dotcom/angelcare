from django.contrib import admin

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from transportation.models import TripRequestStatus

from .models import Trip, TurnDown


@admin.register(Trip)
class TripAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    list_display = (
        "scheduled_pickup_datetime",
        "passenger",
        "service_type",
        "source",
        "broker_trip_number",
        "driver",
        "vehicle",
        "status",
    )
    list_filter = ("status", "source", "service_type", "leg", "will_call", "payer")
    # search_fields is also required here so billing.InvoiceAdmin can
    # autocomplete on `trip` (ADR 0018) -- Django's admin checks
    # enforce this on whatever admin the target model is registered
    # under, not on the referencing admin.
    search_fields = (
        "passenger__legal_name",
        "passenger__phone",
        "broker_trip_number",
        "prior_authorization_number",
        "driver__legal_name",
    )
    autocomplete_fields = ("trip_request", "passenger", "payer", "driver", "vehicle", "outbound_trip")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-scheduled_pickup_datetime",)
    fieldsets = (
        (
            "Rider and route",
            {
                "fields": (
                    "trip_request",
                    "passenger",
                    "service_type",
                    "pickup_address",
                    "dropoff_address",
                ),
                "description": "Creating this trip from a website request? Select it and "
                "the passenger, service type and addresses are filled in for you.",
            },
        ),
        ("When", {"fields": ("scheduled_pickup_datetime", "appointment_datetime", "leg", "outbound_trip", "will_call")}),
        (
            "Source and payer",
            {
                "fields": (
                    "source",
                    "subcontract_partner",
                    "payer",
                    "broker_trip_number",
                    "prior_authorization_number",
                )
            },
        ),
        ("Assignment", {"fields": ("driver", "vehicle", "status", "notes")}),
        ("Record", {"fields": ("id", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_urls(self):
        from .board import board_urls

        return board_urls(self) + super().get_urls()

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

        if change and {"driver", "vehicle"} & set(form.changed_data):
            record_change(
                actor=request.user,
                action="assigned",
                resource_type="Trip",
                resource_id=obj.id,
                before={
                    "driver": _id(form.initial.get("driver")),
                    "vehicle": _id(form.initial.get("vehicle")),
                },
                after={"driver": _id(obj.driver_id), "vehicle": _id(obj.vehicle_id)},
                source="admin",
            )

        if not change and obj.trip_request_id:
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


def _id(value):
    return str(value) if value else None


@admin.register(TurnDown)
class TurnDownAdmin(AngelCareModelAdmin):
    list_display = ("offered_at", "source", "broker_trip_number", "service_type", "reason", "recorded_by")
    list_filter = ("source", "reason", "service_type", "payer")
    search_fields = ("broker_trip_number",)
    autocomplete_fields = ("payer",)
    readonly_fields = ("recorded_by", "created_at")
    ordering = ("-offered_at",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

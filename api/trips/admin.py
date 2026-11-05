from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from unfold.admin import TabularInline

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from transportation.models import TripRequestStatus

from .models import Trip, TripEvent, TripLeg, TurnDown
from .returns import create_return_leg, needs_return_leg


class TripEventInline(TabularInline):
    """
    The driver app's timeline for this trip (ADR 0029). Read-only and
    append-only: it's proof of service, so mistakes are explained in the
    trip's notes, never edited away.
    """

    model = TripEvent
    extra = 0
    can_delete = False
    verbose_name = "timeline step"
    verbose_name_plural = "Timeline (from the driver app)"
    fields = ("occurred_at", "event_type", "reason", "note", "location", "recorded_by")
    readonly_fields = fields
    ordering = ("occurred_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Location")
    def location(self, event):
        if not event.has_location:
            return "— not shared"
        accuracy = f" (±{event.accuracy_m:.0f} m)" if event.accuracy_m is not None else ""
        return format_html(
            '<a href="https://www.google.com/maps?q={},{}" target="_blank" rel="noopener" '
            'class="text-primary-600 underline">{}, {}</a>{}',
            event.latitude, event.longitude, event.latitude, event.longitude, accuracy,
        )


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
    inlines = [TripEventInline]
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
                "description": "Creating this trip from a website request? Select it and leave "
                "passenger, service type and addresses blank: they're copied from the "
                "request when you save. All times are Texas (Central) time.",
            },
        ),
        ("When", {"fields": ("scheduled_pickup_datetime", "appointment_datetime", "leg", "outbound_trip", "will_call", "driver_waits")}),
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
        if not change and needs_return_leg(obj) and obj.leg == TripLeg.ONE_WAY:
            obj.leg = TripLeg.OUTBOUND
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

        if not change and needs_return_leg(obj):
            # Round trip booked on the website (ADR 0032): create the ride home too.
            try:
                back = create_return_leg(obj, actor=request.user)
            except ValidationError as exc:
                self.message_user(
                    request,
                    "The return leg couldn't be created automatically ("
                    + "; ".join(exc.messages)
                    + "). Add it by hand as a Return trip linked to this one.",
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    f"Return leg created for {back.scheduled_pickup_datetime:%b %d}"
                    + (" (estimated time -- adjust when the rider is ready)." if back.will_call or back.driver_waits else "."),
                    level=messages.INFO,
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

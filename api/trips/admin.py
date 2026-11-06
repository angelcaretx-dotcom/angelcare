import base64

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import localize_input
from django.utils.html import format_html, format_html_join
from django.views.decorators.cache import never_cache
from unfold.admin import TabularInline
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from transportation.models import TripRequest, TripRequestStatus

from . import authorizations, standing
from .models import (
    WEEKDAYS,
    PriorAuthorization,
    StandingOrder,
    StandingOrderSkip,
    Trip,
    TripEvent,
    TripEventType,
    TripLeg,
    TripStatus,
    TurnDown,
)
from .proof import build_proof_pdf
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
    fields = ("occurred_at", "event_type", "reason", "note", "location", "signature_shown", "recorded_by")
    readonly_fields = fields
    ordering = ("occurred_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Signature")
    def signature_shown(self, event):
        if event.event_type != TripEventType.PICKED_UP:
            return ""
        if event.signature:
            return format_html(
                '<img src="data:image/png;base64,{}" alt="Rider signature" style="height:48px;background:#fff">{}',
                base64.b64encode(bytes(event.signature)).decode(),
                f" ({event.signed_by_name})" if event.signed_by_name else "",
            )
        return f"Not signed: {event.get_signature_missing_reason_display() or '—'}"

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
    autocomplete_fields = ("trip_request", "passenger", "payer", "facility", "driver", "vehicle", "outbound_trip")
    readonly_fields = ("id", "standing_order_link", "import_link", "authorization_usage", "created_at", "updated_at")
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
                "description": "Creating this trip from a website request? Select it and the "
                "passenger, service type, addresses and pickup time fill in from the request "
                "(only fields still blank). All times are Texas (Central) time.",
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
                    "facility",
                    "broker_trip_number",
                    "prior_authorization_number",
                    "authorization_usage",
                    "billable_miles",
                )
            },
        ),
        ("Assignment", {"fields": ("driver", "vehicle", "status", "notes")}),
        (
            "Record",
            {"fields": ("id", "standing_order_link", "import_link", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Imported from")
    def import_link(self, trip):
        row = getattr(trip, "import_row", None) or getattr(trip, "import_return_row", None)
        if row is None:
            return "—"
        return format_html(
            '<a class="text-primary-600 underline" href="{}">{}, line {}</a>',
            reverse("admin:intake_importrow_review", args=[row.pk]),
            row.batch.original_name,
            row.number,
        )

    @admin.display(description="Standing order")
    def standing_order_link(self, trip):
        if not trip.standing_order_id:
            return "—"
        return format_html(
            '<a class="text-primary-600 underline" href="{}">{}</a>',
            reverse("admin:trips_standingorder_change", args=[trip.standing_order_id]),
            trip.standing_order.reference,
        )

    def get_urls(self):
        from .board import board_urls

        details = path(
            "request-details/<uuid:pk>/",
            self.admin_site.admin_view(self.request_details),
            name="trips_trip_request_details",
        )
        proof = path(
            "<uuid:pk>/proof.pdf",
            self.admin_site.admin_view(never_cache(self.proof_sheet)),
            name="trips_trip_proof",
        )
        return board_urls(self) + [details, proof] + super().get_urls()

    def proof_sheet(self, request, pk):
        """The trip's proof of service as a PDF, built now and never stored (ADR 0034)."""
        trip = get_object_or_404(
            Trip.objects.select_related("passenger", "driver", "vehicle", "payer"), pk=pk
        )
        if not self.has_view_permission(request, trip):
            raise PermissionDenied
        record_change(
            actor=request.user,
            action="exported",
            resource_type="Trip",
            resource_id=trip.id,
            after={"what": "proof_sheet"},
            source="admin",
        )
        day = timezone.localtime(trip.scheduled_pickup_datetime) if trip.scheduled_pickup_datetime else None
        name = f"trip-proof-{day:%Y-%m-%d}-{str(trip.pk)[:8]}.pdf" if day else f"trip-proof-{str(trip.pk)[:8]}.pdf"
        response = HttpResponse(build_proof_pdf(trip), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response

    def request_details(self, request, pk):
        """
        What Add Trip fills in when a request is picked, before saving
        (the same fields fill_from_request copies on save).
        """
        if not self.has_add_permission(request):
            raise PermissionDenied
        trip_request = get_object_or_404(TripRequest.objects.select_related("passenger"), pk=pk)
        record_change(
            actor=request.user,
            action="viewed",
            resource_type="TripRequest",
            resource_id=trip_request.id,
            source="admin",
        )
        pickup = timezone.localtime(trip_request.requested_datetime)
        passenger = trip_request.passenger
        return JsonResponse(
            {
                "passenger": {"id": str(passenger.pk), "text": str(passenger)} if passenger else None,
                "service_type": trip_request.service_type,
                "pickup_address": trip_request.pickup_address,
                "dropoff_address": trip_request.dropoff_address,
                "pickup_date": localize_input(pickup.date()),
                "pickup_time": localize_input(pickup.time().replace(microsecond=0)),
            }
        )

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


@admin.display(description="Authorization")
def _authorization_usage(trip):
    auth = authorizations.find(trip) if trip and not trip._state.adding else None
    if auth is None:
        return "Not tracked (enter it under Prior authorizations to count trips against it)." if trip and             trip.prior_authorization_number else "—"
    left = authorizations.remaining(auth)
    text = f"{authorizations.used(auth)} of {auth.trips_authorized} used, {left} left, until {auth.valid_to:%b %d, %Y}"
    url = reverse("admin:trips_priorauthorization_change", args=[auth.pk])
    if left <= authorizations.LOW_TRIPS:
        return format_html('<a class="underline" href="{}"><strong style="color:#b42318">{}</strong></a>', url, text)
    return format_html('<a class="underline" href="{}">{}</a>', url, text)


TripAdmin.authorization_usage = staticmethod(_authorization_usage)


@admin.register(PriorAuthorization)
class PriorAuthorizationAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """Payers' prior authorizations (ADR 0043): trips used vs. remaining; none scheduled beyond it."""

    list_display = ("number", "passenger", "payer", "valid_from", "valid_to", "usage")
    list_filter = ("payer",)
    search_fields = ("number", "passenger__legal_name")
    autocomplete_fields = ("passenger", "payer")
    readonly_fields = ("usage", "trips", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("number", "passenger", "payer")}),
        ("What it covers", {"fields": ("valid_from", "valid_to", "trips_authorized", "counts", "usage"),
                            "description": "Trips with this number, for this rider and payer, count against it "
                                           "unless cancelled. Dates are Texas dates. A trip can't be saved under "
                                           "this number once every trip is used, for a pickup outside these "
                                           "dates, or for another rider or payer; standing orders stop creating "
                                           "trips when it runs out."}),
        ("Notes", {"fields": ("notes",)}),
        ("Trips", {"fields": ("trips",)}),
        ("Record", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("passenger", "payer")

    @admin.display(description="Used")
    def usage(self, auth):
        if not auth or auth._state.adding:
            return "—"
        left = authorizations.remaining(auth)
        text = f"{authorizations.used(auth)} of {auth.trips_authorized} ({left} left)"
        if left <= authorizations.LOW_TRIPS:
            return format_html('<strong style="color:#b42318">{}</strong>', text)
        return text

    @admin.display(description="Trips counted")
    def trips(self, auth):
        if not auth or auth._state.adding:
            return "—"
        rows = authorizations.counted_trips(auth).order_by("scheduled_pickup_datetime")
        if not rows:
            return "None yet."
        return format_html("<ol>{}</ol>", format_html_join("", '<li><a class="underline" href="{}">{}</a> ({})</li>', (
            (reverse("admin:trips_trip_change", args=[t.pk]),
             timezone.localtime(t.scheduled_pickup_datetime).strftime("%b %d, %Y %I:%M %p"), t.get_status_display())
            for t in rows)))

    def save_model(self, request, obj, form, change):
        if change and {"trips_authorized", "valid_to"} & set(form.changed_data):
            obj.alert_stage = 0  # extended or renewed: alert again when it runs low
        super().save_model(request, obj, form, change)
        record_change(actor=request.user, action="changed" if change else "created",
                      resource_type="PriorAuthorization", resource_id=obj.pk,
                      after={name: str(form.cleaned_data.get(name)) for name in form.changed_data}, source="admin")


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


class StandingOrderForm(forms.ModelForm):
    weekdays = forms.TypedMultipleChoiceField(
        choices=WEEKDAYS,
        coerce=int,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="Days of the week the ride repeats on.",
    )

    class Meta:
        model = StandingOrder
        fields = "__all__"


class StandingOrderSkipInline(TabularInline):
    model = StandingOrderSkip
    extra = 0
    verbose_name = "skipped date"
    verbose_name_plural = "Skipped dates (holiday, hospital stay…) — the series carries on"
    fields = ("date", "reason")


@admin.register(StandingOrder)
class StandingOrderAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Repeating rides (ADR 0035). Saving creates the next 14 days of trips
    (and re-makes upcoming ones nobody has changed yet); a daily job
    keeps them topped up.
    """

    form = StandingOrderForm
    inlines = [StandingOrderSkipInline]
    list_display = ("reference", "passenger", "days", "pickup_time", "return_ride", "authorization", "active")
    list_filter = ("active", "source", "payer", "return_ride")
    search_fields = ("passenger__legal_name", "passenger__phone", "prior_authorization_number")
    autocomplete_fields = ("passenger", "payer", "facility", "driver", "vehicle")
    readonly_fields = ("authorization", "upcoming_trips", "created_at", "updated_at")
    actions = ["create_upcoming_trips"]
    fieldsets = (
        ("Rider and route", {"fields": ("passenger", "service_type", "pickup_address", "dropoff_address")}),
        (
            "When",
            {
                "fields": (
                    "weekdays",
                    "pickup_time",
                    "appointment_time",
                    "return_ride",
                    "return_time",
                    "start_date",
                    "end_date",
                    "active",
                ),
                "description": "Times are Texas (Central) time. Trips are created 14 days ahead.",
            },
        ),
        (
            "Source, payer and authorization",
            {
                "fields": (
                    "source",
                    "subcontract_partner",
                    "payer",
                    "facility",
                    "prior_authorization_number",
                    "prior_authorization_ends",
                    "authorization",
                )
            },
        ),
        ("Regular driver and vehicle", {"fields": ("driver", "vehicle", "notes")}),
        ("Trips", {"fields": ("upcoming_trips",)}),
        ("Record", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Order")
    def reference(self, order):
        return order.reference

    @admin.display(description="Days")
    def days(self, order):
        return order.days_display()

    @admin.display(description="Authorization")
    def authorization(self, order):
        ends = order.prior_authorization_ends
        if not ends:
            return "—"
        today = standing.texas_today()
        if ends < today:
            return format_html('<strong style="color:#b42318">Ended {} — no new trips</strong>', f"{ends:%b %d, %Y}")
        if standing.auth_ending_soon(order, today):
            return format_html('<strong style="color:#9a5b00">Ends {} — renew soon</strong>', f"{ends:%b %d, %Y}")
        return f"Ends {ends:%b %d, %Y}"

    @admin.display(description="Upcoming trips")
    def upcoming_trips(self, order):
        if not order.pk:
            return "Created when you save."
        trips = (
            order.trips.filter(standing_date__gte=standing.texas_today())
            .select_related("driver")
            .order_by("scheduled_pickup_datetime")[:30]
        )
        if not trips:
            return "None in the next 14 days."
        rows = format_html_join(
            "",
            '<li><a class="text-primary-600 underline" href="{}">{}</a> — {}{}</li>',
            (
                (
                    reverse("admin:trips_trip_change", args=[t.pk]),
                    f"{timezone.localtime(t.scheduled_pickup_datetime):%a %b %d, %I:%M %p}".replace(" 0", " "),
                    t.get_leg_display(),
                    _upcoming_note(t),
                )
                for t in trips
            ),
        )
        return format_html("<ul>{}</ul>", rows)

    def save_related(self, request, form, formsets, change):
        # After the skipped dates are saved, so they count.
        super().save_related(request, form, formsets, change)
        self._make_trips(request, form.instance)

    def _make_trips(self, request, order):
        try:
            made, kept = standing.refresh(order, actor=request.user)
        except ValidationError as exc:
            self.message_user(
                request,
                "Saved, but trips couldn't be created: " + "; ".join(exc.messages),
                level=messages.WARNING,
            )
            return
        days = len({t.standing_date for t in made})
        text = f"Trips created for {days} upcoming date{'s' if days != 1 else ''}." if days else "No new trips needed."
        if kept:
            text += f" {kept} upcoming trip{'s' if kept != 1 else ''} you had already changed {'were' if kept != 1 else 'was'} kept as is."
        self.message_user(request, text, level=messages.INFO)

    @admin.action(description="Create upcoming trips now")
    def create_upcoming_trips(self, request, queryset):
        total = 0
        for order in queryset:
            try:
                total += len(standing.generate(order, actor=request.user))
            except ValidationError as exc:
                self.message_user(request, f"{order.reference}: " + "; ".join(exc.messages), level=messages.WARNING)
        self.message_user(request, f"{total} trip{'s' if total != 1 else ''} created.", level=messages.INFO)


def _upcoming_note(trip):
    if trip.status != TripStatus.SCHEDULED:
        return f" · {trip.get_status_display()}"
    return f" · {trip.driver}" if trip.driver_id else " · unassigned"

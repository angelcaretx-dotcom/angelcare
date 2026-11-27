from django.contrib import admin, messages
from django.utils import timezone

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from billing import checkout, payments

from .models import TripRequest, TripRequestStatus

# A request that stops here is not happening: nothing more may be paid for it (ADR 0050).
CLOSED = (TripRequestStatus.CANCELLED, TripRequestStatus.DECLINED)


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
    readonly_fields = ("id", "created_at", "updated_at", "source", "facility", "facility_user", "price_emailed")
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

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        # The price is a billing figure (ADR 0047): shown only to staff who may see quotes.
        if obj is not None and request.user.has_perm("billing.view_quote"):
            fieldsets = (*fieldsets[:1], ("Price", {"fields": ("price_emailed",)}), *fieldsets[1:])
        return fieldsets

    @admin.display(description="Price emailed")
    def price_emailed(self, trip_request):
        quote = trip_request.quotes.filter(delivered=True).first()
        if quote is None:
            return "Not yet. Use “Email the price” at the top of this page."
        when = timezone.localtime(quote.created_at)
        text = f"${quote.amount} to {quote.sent_to} on {when:%b %d, %Y at %I:%M %p}"
        # Paid by card through the link in that email (ADR 0050).
        paid = payments.paid_payment(trip_request)
        if paid is not None and paid.paid_at:
            paid_on = f"{timezone.localtime(paid.paid_at):%b %d, %Y}"
            if paid.quote_id == quote.pk:
                text += f". Paid online on {paid_on}."
            else:
                text += f". An earlier price (${paid.quote.amount}) was paid online on {paid_on}."
        return text

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
            if obj.status in CLOSED:
                # A payment page still open for this ride is closed. A ride already paid is not
                # refunded here: refunds are made in Stripe, and the dashboard lists it until then.
                checkout.expire_open(obj)
                if payments.request_paid(obj):
                    self.message_user(
                        request,
                        "This ride was paid online: a refund may be due. Refunds are made in Stripe; "
                        "the payment is listed under “Payments needing attention” until then.",
                        messages.WARNING,
                    )

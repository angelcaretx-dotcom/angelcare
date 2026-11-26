import csv
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.cache import never_cache

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from drivers.models import Driver
from payers.models import Payer
from transportation.models import ReturnRide, TripRequest

from . import pricing, quotes, reports
from .models import DriverPayPlan, Invoice, Quote, RateCard

INPUT = "border border-base-200 px-2 py-1 rounded-default text-sm dark:bg-base-900 dark:border-base-800"
PRIVATE = "private"


def _lines_html(lines):
    """How an amount was worked out, line by line (invoices and quotes)."""
    return format_html(
        "<ul>{}</ul>",
        format_html_join("", "<li>{}: {} × ${} = <strong>${}</strong></li>",
                         ((l["label"], l["quantity"], l["unit"], l["amount"]) for l in lines)),
    )


def can_send_quotes(user) -> bool:
    """Emailing a price shows rates and a rider's request: both permissions are needed (ADR 0047)."""
    return user.has_perm("billing.add_quote") and user.has_perm("transportation.view_triprequest")


def _last_week():
    today = pricing.local(timezone.now()).date()
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


class InvoiceBatchForm(forms.Form):
    payer = forms.ChoiceField(help_text="Whose completed trips to invoice.")
    start = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="To", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payer"].choices = [(PRIVATE, "Private pay (riders with no payer)")] + [
            (str(p.pk), p.name) for p in Payer.objects.order_by("name")
        ]

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError("The end date is before the start date.")
        return data

    def payer_obj(self):
        value = self.cleaned_data["payer"]
        return None if value == PRIVATE else Payer.objects.get(pk=value)


@admin.register(Invoice)
class InvoiceAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    list_display = (
        "id",
        "trip",
        "payer",
        "amount",
        "status",
        "issued_date",
        "paid_date",
        "created_at",
    )
    list_filter = ("status", "payer")
    search_fields = ("trip__passenger__legal_name", "trip__broker_trip_number",)
    autocomplete_fields = ("trip", "payer")
    readonly_fields = ("id", "rate_card", "calculation", "created_at", "updated_at")
    exclude = ("lines",)
    ordering = ("-created_at",)

    @admin.display(description="How it was calculated")
    def calculation(self, invoice):
        if not invoice.lines:
            return "Entered by hand."
        return _lines_html(invoice.lines)

    def get_urls(self):
        return [
            path("create-batch/", self.admin_site.admin_view(never_cache(self.batch_view)),
                 name="billing_invoice_batch"),
            path("profit/", self.admin_site.admin_view(never_cache(self.profit_view)), name="billing_invoice_profit"),
        ] + super().get_urls()

    def profit_view(self, request):
        """Trip profit (ADR 0041): charge - driver pay - mileage cost, by payer, for a date range."""
        if not (self.has_view_permission(request) and request.user.has_perm("billing.view_driverpayplan")):
            raise PermissionDenied
        start, end = _last_week()
        form = ProfitForm(request.GET or None, initial={"start": start, "end": end})
        result = None
        if form.is_bound and form.is_valid():
            start, end = form.cleaned_data["start"], form.cleaned_data["end"]
            result = reports.profit(start, end, cost_per_mile=form.cleaned_data["cost_per_mile"])
            record_change(actor=request.user, action="viewed", resource_type="ProfitReport", resource_id=f"{start}..{end}",
                          after={"from": str(start), "to": str(end)}, source="admin")
            if request.GET.get("format") == "csv":
                return _profit_csv(start, end, result)
        return TemplateResponse(request, "admin/billing/profit.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Trip profit", "form": form, "result": result, "input_class": INPUT,
            "query": request.GET.urlencode(),
        })

    def batch_view(self, request):
        """'Create invoices for completed trips' (ADR 0039): drafts to check, with what couldn't be billed and why."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        start, end = _last_week()
        form = InvoiceBatchForm(request.POST or None, initial={"start": start, "end": end})
        made = skipped = None
        if request.method == "POST" and form.is_valid():
            made, skipped = pricing.create_invoices(
                form.payer_obj(), form.cleaned_data["start"], form.cleaned_data["end"], user=request.user
            )
            self.message_user(request, f"{len(made)} draft invoice{'s' if len(made) != 1 else ''} created.",
                              messages.SUCCESS if made else messages.INFO)
        return TemplateResponse(request, "admin/billing/invoice_batch.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Create invoices for completed trips", "form": form, "made": made, "skipped": skipped,
            "input_class": INPUT,
        })

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Invoice",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )


class AuditedSaveMixin:
    """Rates and pay are money: every add and change is audited with its values."""

    audit_name = ""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_change(
            actor=request.user,
            action="changed" if change else "created",
            resource_type=self.audit_name,
            resource_id=obj.pk,
            after={name: str(form.cleaned_data.get(name)) for name in form.changed_data},
            source="admin",
        )


@admin.register(RateCard)
class RateCardAdmin(AuditedSaveMixin, AngelCareModelAdmin):
    """What each payer is charged (ADR 0039). A price change: add a new card with a later start date."""

    audit_name = "RateCard"
    list_display = ("name", "payer_label", "effective_from", "effective_to", "ambulatory", "wheelchair")
    list_filter = ("payer",)
    search_fields = ("name", "payer__name")
    autocomplete_fields = ("payer",)
    fieldsets = (
        (None, {"fields": ("name", "payer", "effective_from", "effective_to"),
                "description": "Times are Texas (Central) time. A blank booking fee means that service isn't billed "
                               "on this card. Invoices and emailed prices already made keep the rates they used: "
                               "for a price change, add a new card with a later start date."}),
        ("Ambulatory", {"fields": ("base_ambulatory", "loading_ambulatory", "per_mile_ambulatory",
                                   "wait_rate_ambulatory")}),
        ("Wheelchair", {"fields": ("base_wheelchair", "loading_wheelchair", "per_mile_wheelchair",
                                   "wait_rate_wheelchair")}),
        ("Medical supply & equipment delivery", {"fields": ("base_equipment_delivery", "loading_equipment_delivery",
                                                            "per_mile_equipment_delivery")}),
        ("Round trips", {"fields": ("fees_on_return",)}),
        ("Mileage (any service with no per-mile rate of its own)", {"fields": ("per_mile", "included_miles")}),
        ("Wait time (wait-and-return rides)", {"fields": ("wait_free_minutes", "wait_block_minutes",
                                                          "wait_per_minute")}),
        ("No-shows and after-hours", {"fields": ("no_show_fee", "after_hours_fee", "after_hours_start",
                                                 "after_hours_end", "weekends_after_hours")}),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_changeform_initial_data(self, request):
        # A new card starts with the owner's private-pay rules (ADR 0048): fees once
        # for a round trip, 15 free minutes, then 15-minute wait blocks. A payer's
        # contract card can say otherwise.
        return {"fees_on_return": False, "wait_free_minutes": 15, "wait_block_minutes": 15,
                **super().get_changeform_initial_data(request)}

    @admin.display(description="Payer")
    def payer_label(self, card):
        return card.payer or "Private pay"

    @admin.display(description="Ambulatory")
    def ambulatory(self, card):
        return _service_summary(card, "ambulatory")

    @admin.display(description="Wheelchair")
    def wheelchair(self, card):
        return _service_summary(card, "wheelchair")


def _service_summary(card, service) -> str:
    """One service's rates on the list page, e.g. '$40.00 + $55.00 loading + $3.00/mi'."""
    base = card.base_for(service)
    if base is None:
        return "Not billed"
    parts = [f"${base}"]
    if card.loading_for(service):
        parts.append(f"${card.loading_for(service)} loading")
    if card.per_mile_for(service):
        parts.append(f"${card.per_mile_for(service)}/mi")
    return " + ".join(parts)


class QuoteForm(forms.Form):
    miles = forms.DecimalField(required=False, min_value=0, max_digits=7, decimal_places=2, label="One-way miles")
    wait_minutes = forms.IntegerField(required=False, min_value=0, max_value=1440, label="Expected wait (minutes)")


@admin.register(Quote)
class QuoteAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Prices emailed to customers (ADR 0047). A record of what each customer
    was told: never added, edited or deleted here. A price is sent from the
    trip request's page ("Email the price"); a new price is a new quote.
    """

    list_display = ("created_at", "reference", "amount", "rate_card", "sent_to", "sent_by", "delivered")
    list_filter = ("delivered", "rate_card")
    search_fields = ("sent_to", "trip_request__full_name")
    list_select_related = ("rate_card", "sent_by")
    fields = ("request_link", "amount", "calculation", "miles", "wait_minutes", "rate_card", "sent_to", "sent_by",
              "delivered", "created_at")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Request")
    def reference(self, quote):
        return f"#{str(quote.trip_request_id)[:8].upper()}"

    @admin.display(description="Trip request")
    def request_link(self, quote):
        return format_html(
            '<a href="{}" class="text-primary-600 underline">{}</a>',
            reverse("admin:transportation_triprequest_change", args=[quote.trip_request_id]), self.reference(quote),
        )

    @admin.display(description="How it was calculated")
    def calculation(self, quote):
        return _lines_html(quote.lines)

    def get_urls(self):
        return [
            path("send/<uuid:request_id>/", self.admin_site.admin_view(never_cache(self.send_view)),
                 name="billing_quote_send"),
        ] + super().get_urls()

    def send_view(self, request, request_id):
        """'Email the price' for one trip request (ADR 0047): work it out, check it, then send the total."""
        if not can_send_quotes(request.user):
            raise PermissionDenied
        trip_request = get_object_or_404(TripRequest.objects.select_related("passenger__payer"), pk=request_id)
        if request.method == "GET":
            # The page shows the rider's request (ADR 0027).
            record_change(actor=request.user, action="viewed", resource_type="TripRequest",
                          resource_id=trip_request.pk, source="admin")
        last = trip_request.quotes.first()
        form = QuoteForm(request.POST or None,
                         initial={"miles": last.miles, "wait_minutes": last.wait_minutes} if last else None)
        priced = error = None
        if request.method == "POST" and form.is_valid():
            miles, wait = form.cleaned_data["miles"], form.cleaned_data["wait_minutes"] or 0
            try:
                card, lines, notes = pricing.quote(trip_request, miles=miles, wait_minutes=wait)
                amount = pricing.total(lines)
                sending = "send" in request.POST
                # Only the price staff just checked is ever sent: a rate card edited in between shows again first.
                if sending and request.POST.get("checked_amount") == str(amount):
                    quote = quotes.send(trip_request, miles=miles, wait_minutes=wait, user=request.user)
                    if quote.delivered:
                        self.message_user(request, f"${quote.amount} emailed to {quote.sent_to}.", messages.SUCCESS)
                    else:
                        self.message_user(
                            request,
                            f"The email to {quote.sent_to} couldn't be sent, so the customer has NOT received "
                            "this price. Check the email log, then send it again.",
                            messages.ERROR,
                        )
                    return HttpResponseRedirect(
                        reverse("admin:transportation_triprequest_change", args=[trip_request.pk])
                    )
                priced = {"card": card, "lines": lines, "notes": notes, "amount": amount, "miles": miles,
                          "wait_minutes": wait, "changed": sending}
            except pricing.PricingError as exc:
                error = str(exc)
        return TemplateResponse(request, "admin/billing/quote_send.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Email the price", "trip_request": trip_request, "form": form, "priced": priced, "error": error,
            "waits": trip_request.return_ride == ReturnRide.WAIT,
            "sent": trip_request.quotes.select_related("sent_by"), "input_class": INPUT,
            "request_url": reverse("admin:transportation_triprequest_change", args=[trip_request.pk]),
        })


class ProfitForm(forms.Form):
    start = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="To", widget=forms.DateInput(attrs={"type": "date"}))
    cost_per_mile = forms.DecimalField(required=False, min_value=0, max_digits=6, decimal_places=3,
                                       help_text="Fuel, wear and insurance per mile, if you want it counted.")

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError("The end date is before the start date.")
        return data


class YearEndForm(forms.Form):
    year = forms.IntegerField(min_value=2020, max_value=2100)
    threshold = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2,
                                   help_text="Leave blank for the IRS 1099-NEC floor for that year.")


class PayStatementForm(forms.Form):
    driver = forms.ModelChoiceField(queryset=Driver.objects.order_by("legal_name"))
    start = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="To", widget=forms.DateInput(attrs={"type": "date"}))


@admin.register(DriverPayPlan)
class DriverPayPlanAdmin(AuditedSaveMixin, AngelCareModelAdmin):
    """How each driver is paid (ADR 0039). A change: add a new plan with a later start date."""

    audit_name = "DriverPayPlan"
    list_display = ("driver", "effective_from", "effective_to", "per_trip", "per_mile", "hourly", "percent_of_charge")
    list_filter = ("driver",)
    search_fields = ("driver__legal_name",)
    autocomplete_fields = ("driver",)
    fieldsets = (
        (None, {"fields": ("driver", "effective_from", "effective_to"),
                "description": "Use any mix: the driver earns the sum. Trips that completed or were no-shows count."}),
        ("Pay", {"fields": ("per_trip", "per_trip_wheelchair", "per_mile", "hourly", "percent_of_charge")}),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_urls(self):
        return [
            path("statement/", self.admin_site.admin_view(never_cache(self.statement_view)),
                 name="billing_driverpayplan_statement"),
            path("year-end/", self.admin_site.admin_view(never_cache(self.year_end_view)),
                 name="billing_driverpayplan_year_end"),
        ] + super().get_urls()

    def year_end_view(self, request):
        """1099 year-end totals (ADR 0041): each contractor's calculated pay for a calendar year."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        last_year = pricing.local(timezone.now()).year - 1
        form = YearEndForm(request.GET or None, initial={"year": last_year})
        result = threshold = year = None
        if form.is_bound and form.is_valid():
            year = form.cleaned_data["year"]
            threshold = form.cleaned_data["threshold"]
            if threshold is None:
                threshold = reports.default_threshold(year)
            result = reports.year_end(year, threshold)
            record_change(actor=request.user, action="viewed", resource_type="YearEndTotals", resource_id=str(year),
                          after={"year": year}, source="admin")
            if request.GET.get("format") == "csv":
                return _year_end_csv(year, threshold, result)
        return TemplateResponse(request, "admin/billing/year_end.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "1099 year-end totals", "form": form, "result": result, "year": year,
            "threshold": threshold, "input_class": INPUT, "query": request.GET.urlencode(),
        })

    def statement_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        start, end = _last_week()
        form = PayStatementForm(request.GET or None, initial={"start": start, "end": end})
        result = None
        if form.is_bound and form.is_valid():
            driver, start, end = (form.cleaned_data[k] for k in ("driver", "start", "end"))
            result = pricing.statement(driver, start, end)
            record_change(actor=request.user, action="viewed", resource_type="DriverPayStatement",
                          resource_id=driver.pk, after={"from": str(start), "to": str(end)}, source="admin")
            if request.GET.get("format") == "csv":
                return _statement_csv(driver, start, end, result)
        return TemplateResponse(request, "admin/billing/pay_statement.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Driver pay statement", "form": form, "result": result, "input_class": INPUT,
            "query": request.GET.urlencode(),
        })


def _statement_csv(driver, start, end, result) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="pay-{driver.legal_name.replace(" ", "-")}-{start}-{end}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Date", "Item", "Quantity", "Rate", "Amount", "Notes"])
    for row in result["rows"]:
        when = pricing.local(row["trip"].scheduled_pickup_datetime).strftime("%Y-%m-%d %H:%M")
        for line in row["lines"] or [None]:
            writer.writerow([when, f"Trip {str(row['trip'].pk)[:8]} " + (line.label if line else ""),
                             line.quantity if line else "", line.unit if line else "", line.amount if line else "0.00",
                             "; ".join(row["notes"])])
    for hours in result["hours"]:
        line = hours["line"]
        writer.writerow([hours["check"].service_date, line.label, line.quantity, line.unit, line.amount, ""])
    writer.writerow(["", "Total", "", "", result["total"], "; ".join(result["notes"])])
    return response


def _profit_csv(start, end, result) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="profit-{start}-{end}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Date", "Trip", "Payer", "Driver", "Charge", "Driver pay", "Mileage cost", "Profit", "Notes"])
    for row in result["rows"]:
        trip = row["trip"]
        writer.writerow([pricing.local(trip.scheduled_pickup_datetime).strftime("%Y-%m-%d %H:%M"), str(trip.pk)[:8],
                         trip.payer.name if trip.payer_id else reports.PRIVATE,
                         trip.driver.legal_name if trip.driver_id else "",
                         *("" if row[k] is None else row[k] for k in ("charge", "pay", "mileage", "profit")),
                         "; ".join(row["notes"])])
    writer.writerow(["", "", "", "Hourly pay (all drivers)", "", result["hourly"], "", "", "; ".join(result["notes"])])
    writer.writerow(["", "", "", "Total", result["revenue"], "", "", result["total"],
                     f"{len(result['incomplete'])} trip(s) not counted"])
    return response


def _year_end_csv(year, threshold, result) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="1099-totals-{year}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Driver", "Trips", f"Calculated pay {year}", f"At or over ${threshold}", "Trips/days not paid"])
    for row in result:
        writer.writerow([row["driver"].legal_name, row["trips"], row["total"], "yes" if row["reportable"] else "no",
                         row["flagged"]])
    return response

import csv
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.cache import never_cache

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from drivers.models import Driver
from payers.models import Payer

from . import pricing
from .models import DriverPayPlan, Invoice, RateCard

INPUT = "border border-base-200 px-2 py-1 rounded-default text-sm dark:bg-base-900 dark:border-base-800"
PRIVATE = "private"


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
        return format_html(
            "<ul>{}</ul>",
            format_html_join("", "<li>{}: {} × ${} = <strong>${}</strong></li>",
                             ((l["label"], l["quantity"], l["unit"], l["amount"]) for l in invoice.lines)),
        )

    def get_urls(self):
        return [
            path("create-batch/", self.admin_site.admin_view(never_cache(self.batch_view)),
                 name="billing_invoice_batch"),
        ] + super().get_urls()

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
    list_display = ("name", "payer_label", "effective_from", "effective_to", "base_ambulatory", "base_wheelchair",
                    "per_mile")
    list_filter = ("payer",)
    search_fields = ("name", "payer__name")
    autocomplete_fields = ("payer",)
    fieldsets = (
        (None, {"fields": ("name", "payer", "effective_from", "effective_to"),
                "description": "Times are Texas (Central) time. A blank base rate means that service isn't billed "
                               "on this card. Invoices already made keep the card they used."}),
        ("Base rate per trip", {"fields": ("base_ambulatory", "base_wheelchair", "base_stretcher",
                                           "base_equipment_delivery")}),
        ("Mileage", {"fields": ("per_mile", "included_miles")}),
        ("Wait time (wait-and-return rides)", {"fields": ("wait_free_minutes", "wait_per_minute")}),
        ("No-shows and after-hours", {"fields": ("no_show_fee", "after_hours_fee", "after_hours_start",
                                                 "after_hours_end", "weekends_after_hours")}),
        ("Notes", {"fields": ("notes",)}),
    )

    @admin.display(description="Payer")
    def payer_label(self, card):
        return card.payer or "Private pay"


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
        ] + super().get_urls()

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

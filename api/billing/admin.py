import csv
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
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
from transportation import distance
from transportation.models import ReturnRide, TripRequest

from . import checkout, payments, pricing, quotes, reports
from .models import DriverPayPlan, Invoice, Payment, PaymentStatus, PaymentTerms, Quote, RateCard

INPUT = "border border-base-200 px-2 py-1 rounded-default text-sm dark:bg-base-900 dark:border-base-800"
PRIVATE = "private"


def _line_html(line):
    if line.get("kind") == "discount":
        # A discount (ADR 0051): what was taken off and, for the staff reading this page, why.
        # On an invoice it is the trip's share of the discount, with no percentage or reason.
        label = f"Discount ({line['percent']}%)" if line.get("percent") else "Discount"
        reason = format_html(" Reason: {}", line["reason"]) if line.get("reason") else ""
        return format_html("<li>{}: <strong>-${}</strong>{}</li>", label, str(line["amount"]).lstrip("-"), reason)
    return format_html("<li>{}: {} × ${} = <strong>${}</strong></li>",
                       line["label"], line["quantity"], line["unit"], line["amount"])


def _lines_html(lines):
    """How an amount was worked out, line by line (invoices and quotes)."""
    return format_html("<ul>{}</ul>", format_html_join("", "{}", ((_line_html(line),) for line in lines)))


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
    # The two confirmations a send needs (ADR 0049). Not required here, so that working
    # the price out doesn't need them: the view and quotes.send refuse a send without both.
    miles_checked = forms.BooleanField(required=False)
    customer_pays = forms.BooleanField(required=False)
    # An optional discount (ADR 0051). The rules (greater than 0, at most 100%, at most the price,
    # two decimals, a reason) are pricing.discount_line's, so the page and quotes.send agree.
    discount = forms.DecimalField(required=False, max_digits=14, decimal_places=6, label="Discount")
    discount_kind = forms.ChoiceField(required=False, choices=[(kind, kind) for kind in pricing.DISCOUNT_KINDS])
    discount_reason = forms.CharField(
        required=False, max_length=pricing.MAX_DISCOUNT_REASON, label="Reason for the discount",
        error_messages={"max_length": f"The reason can be at most {pricing.MAX_DISCOUNT_REASON} characters."},
    )

    def discount_asked(self) -> pricing.Discount | None:
        """The discount typed in the form, or None. A reason typed without a discount is ignored."""
        value = self.cleaned_data.get("discount")
        if value is None:
            return None
        return pricing.Discount(kind=self.cleaned_data.get("discount_kind") or pricing.DISCOUNT_DOLLARS,
                                value=value, reason=self.cleaned_data.get("discount_reason") or "")


@admin.register(Quote)
class QuoteAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Prices emailed to customers (ADR 0047). A record of what each customer
    was told: never added, edited or deleted here. A price is sent from the
    trip request's page ("Email the price"); a new price is a new quote.
    """

    list_display = ("created_at", "reference", "amount", "rate_card", "sent_to", "sent_by", "delivered",
                    "paid_online")
    list_filter = ("delivered", "rate_card")
    search_fields = ("sent_to", "trip_request__full_name")
    list_select_related = ("rate_card", "sent_by")
    fields = ("request_link", "amount", "calculation", "miles", "wait_minutes", "rate_card", "sent_to", "sent_by",
              "delivered", "paid_online", "created_at")
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

    def get_changelist_instance(self, request):
        # One read for the whole page says which of its prices were paid online (ADR 0050). A
        # missing payment table reads as "none paid": this list must work before the database update.
        changelist = super().get_changelist_instance(request)
        quotes_shown = list(changelist.result_list)
        paid = payments.safe_read(
            lambda: dict(payments.holding(Payment.objects.filter(quote__in=[q.pk for q in quotes_shown]))
                         .order_by("-paid_at").values_list("quote_id", "paid_at")),
            {},
        )
        for quote in quotes_shown:
            quote.paid_online_at = paid.get(quote.pk)
        return changelist

    @admin.display(description="Paid online")
    def paid_online(self, quote):
        if hasattr(quote, "paid_online_at"):
            paid_at = quote.paid_online_at
        else:
            paid_at = payments.safe_read(
                lambda: payments.holding(quote.payments.all()).values_list("paid_at", flat=True).first(), None
            )
        return f"Paid online on {timezone.localtime(paid_at):%b %d, %Y}" if paid_at else "-"

    def get_urls(self):
        return [
            path("send/<uuid:request_id>/", self.admin_site.admin_view(never_cache(self.send_view)),
                 name="billing_quote_send"),
        ] + super().get_urls()

    def send_view(self, request, request_id):
        """
        'Email the price' for one trip request (ADR 0047, 0049). The page opens with the price
        already worked out from the estimated miles; the administrator checks the miles on the
        map, confirms who pays, and sends.
        """
        if not can_send_quotes(request.user):
            raise PermissionDenied
        trip_request = get_object_or_404(
            TripRequest.objects.select_related("passenger__payer", "facility"), pk=request_id
        )
        if request.method == "GET":
            # The page shows the rider's request (ADR 0027).
            record_change(actor=request.user, action="viewed", resource_type="TripRequest",
                          resource_id=trip_request.pk, source="admin")
        estimate = distance.estimate(trip_request)
        last = trip_request.quotes.first()
        # The miles of the last price sent, else the estimate from the two ZIP codes.
        initial = {"miles": last.miles if last and last.miles is not None else estimate.miles,
                   "wait_minutes": last.wait_minutes if last else None}
        form = QuoteForm(request.POST or None, initial=initial)
        priced = error = None
        # (miles, wait, discount) to price: what was posted, or on a GET what the miles box starts with.
        # A discount is never carried over from an earlier price: it is typed each time, with its reason.
        asked = None
        # A price that was paid online is final (ADR 0050): the page says so instead of offering a new one.
        paid = payments.paid_payment(trip_request)
        if paid is not None:
            error = quotes.ALREADY_PAID
        elif request.method == "POST":
            if form.is_valid():
                asked = form.cleaned_data["miles"], form.cleaned_data["wait_minutes"] or 0, form.discount_asked()
        elif initial["miles"] is not None:
            asked = initial["miles"], initial["wait_minutes"] or 0, None
        if asked is not None:
            miles, wait, discount = asked
            try:
                # A discount that can't be given raises here, with the reason: nothing is priced or sent.
                card, lines, notes = pricing.quote(trip_request, miles=miles, wait_minutes=wait, discount=discount)
                # The total after the discount: the amount that is checked, shown on the button and sent.
                amount = pricing.total(lines)
                sending = request.method == "POST" and "send" in request.POST
                # Only the price staff just checked is ever sent: a rate card edited in between, or a
                # discount changed without "Work out the price", shows the new price again first.
                same_price = sending and request.POST.get("checked_amount") == str(amount)
                confirmed = form.is_bound and form.cleaned_data["miles_checked"] and form.cleaned_data["customer_pays"]
                if same_price and confirmed:
                    quote = quotes.send(trip_request, miles=miles, wait_minutes=wait, user=request.user,
                                        miles_checked=True, customer_pays=True, discount=discount)
                    if getattr(quote, "already_sent", False):
                        # A second press of the button: nothing went out, so no second "emailed" message.
                        self.message_user(
                            request,
                            f"This price was already emailed to {quote.sent_to} a moment ago. It was not sent again.",
                            messages.INFO,
                        )
                    elif quote.delivered:
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
                taken_off = next((line for line in lines if line.kind == "discount"), None)
                charges = [line for line in lines if line.kind != "discount"]
                priced = {"card": card, "lines": charges, "notes": notes, "amount": amount, "miles": miles,
                          "wait_minutes": wait, "changed": sending and not same_price,
                          "unconfirmed": quotes.NOT_CONFIRMED if same_price else "",
                          # The discount, shown after the lines and before the total (ADR 0051).
                          "discount": None if taken_off is None else {
                              "off": -taken_off.amount, "percent": taken_off.extra["percent"],
                              "reason": taken_off.extra["reason"], "before": pricing.total(charges),
                          }}
            except pricing.PricingError as exc:
                error = str(exc)
        try:
            rules_card = pricing.quote_card(trip_request)
            rules = pricing.explain(rules_card, trip_request)
        except pricing.PricingError as exc:
            # No card to explain, so no price either: say why as soon as the page opens.
            rules_card, rules, error = None, [], error or str(exc)
        return TemplateResponse(request, "admin/billing/quote_send.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Email the price", "trip_request": trip_request, "form": form, "priced": priced, "error": error,
            "waits": trip_request.return_ride == ReturnRide.WAIT,
            "round_trip": trip_request.return_ride != ReturnRide.NONE,
            "estimate": estimate, "map_url": distance.map_url(trip_request),
            "miles_from_last": bool(last and last.miles is not None), "rules": rules, "rules_card": rules_card,
            "sent": trip_request.quotes.select_related("sent_by"), "input_class": INPUT,
            "request_url": reverse("admin:transportation_triprequest_change", args=[trip_request.pk]),
            "paid": paid,
            # Every attempt to pay a price of this request online (ADR 0050), newest first.
            "online_payments": payments.safe_read(
                lambda: list(Payment.objects.filter(quote__trip_request=trip_request).order_by("-created_at")), []
            ),
            "can_view_payments": request.user.has_perm("billing.view_payment"),
        })


TEST_MODE_NOTICE = "Stripe TEST MODE: payments made now are test payments, not real money."


class TestModeNoticeMixin:
    """Says "Stripe TEST MODE" on the payment pages while the server holds a test key (ADR 0050)."""

    def _test_mode_notice(self, request):
        if request.method == "GET" and payments.test_mode():
            self.message_user(request, TEST_MODE_NOTICE, messages.WARNING)

    def changelist_view(self, request, extra_context=None):
        self._test_mode_notice(request)
        return super().changelist_view(request, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        self._test_mode_notice(request)
        return super().change_view(request, object_id, form_url, extra_context)


def _money(amount) -> str:
    return "" if amount is None else f"${amount:,.2f}"


@admin.register(Payment)
class PaymentAdmin(TestModeNoticeMixin, AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    What customers paid online (ADR 0050). A record of what Stripe reported:
    never added, edited or deleted here. Refunds are made in Stripe, and
    show up on the payment once Stripe reports them.
    """

    list_display = ("created_at", "reference", "status", "asked", "paid_amount", "refunded_amount", "paid_at")
    list_filter = ("status",)
    # The reference is the start of the request's id, so "#3FA9C2D1" finds its payments.
    search_fields = ("^quote__trip_request__id", "=session_id", "=payment_intent_id")
    search_help_text = "Search by the request's reference number, for example #3FA9C2D1."
    list_select_related = ("quote",)
    fields = ("request_link", "quote_link", "status", "attention_reason", "attempt", "asked", "paid_amount",
              "paid_at", "refunded_amount", "refunded_at", "disputed_at", "kind", "session_id", "payment_intent_id",
              "expires_at", "office_notified_at", "customer_notified_at", "created_at", "updated_at")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_search_results(self, request, queryset, search_term):
        # People type the reference the way it is printed, with its "#".
        return super().get_search_results(request, queryset, search_term.replace("#", " ").strip())

    # "Check with Stripe" is offered on a payment that isn't settled.
    CHECKABLE = (PaymentStatus.OPEN, PaymentStatus.NEEDS_ATTENTION)

    def get_urls(self):
        return [
            path("<uuid:payment_id>/check/", self.admin_site.admin_view(never_cache(self.check_view)),
                 name="billing_payment_check"),
        ] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        payment = self.get_object(request, object_id)
        can_check = bool(payment and payment.session_id and payment.status in self.CHECKABLE)
        return super().change_view(request, object_id, form_url, {**(extra_context or {}), "can_check": can_check})

    def check_view(self, request, payment_id):
        """
        "Check with Stripe": ask Stripe where this payment stands and record the answer, the same
        way the webhook does. For a payment whose report never arrived, and for one that needed
        attention once its cause is fixed (for example a request cancelled by mistake and reopened).
        """
        if not self.has_view_permission(request):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        payment = get_object_or_404(Payment, pk=payment_id)
        back = HttpResponseRedirect(reverse("admin:billing_payment_change", args=[payment.pk]))
        if not payment.session_id or payment.status not in self.CHECKABLE:
            self.message_user(request, "There is nothing to check on this payment.", messages.INFO)
            return back
        before = payment.status
        try:
            state = payments.get_provider().retrieve_session(payment.session_id)
            after = checkout.apply_session(state, actor=request.user, recheck=True)
        except payments.PaymentError as exc:
            # The message of a PaymentError is written for staff and holds nothing secret.
            self.message_user(request, f"Stripe could not be asked. {exc}", messages.ERROR)
            return back
        record_change(actor=request.user, action="payment_checked", resource_type="Payment", resource_id=payment.pk,
                      before={"status": before}, after={"status": after}, source="admin")
        if after == PaymentStatus.PAID:
            self.message_user(request, "Stripe says this payment is paid. It is recorded as paid.", messages.SUCCESS)
        elif after == PaymentStatus.NEEDS_ATTENTION:
            self.message_user(
                request,
                "Stripe says the money arrived, but this payment still needs attention: see the reason below.",
                messages.WARNING,
            )
        elif after == before:
            self.message_user(request, "Stripe says nothing has changed: this payment has not been paid.",
                              messages.INFO)
        else:
            payment.refresh_from_db()
            self.message_user(request, f"Recorded what Stripe says: {payment.get_status_display()}.", messages.INFO)
        return back

    @admin.display(description="Request")
    def reference(self, payment):
        return payments.reference(payment.quote.trip_request_id)

    @admin.display(description="Trip request")
    def request_link(self, payment):
        return format_html(
            '<a href="{}" class="text-primary-600 underline">{}</a>',
            reverse("admin:transportation_triprequest_change", args=[payment.quote.trip_request_id]),
            self.reference(payment),
        )

    @admin.display(description="Price emailed")
    def quote_link(self, payment):
        return format_html(
            '<a href="{}" class="text-primary-600 underline">{}</a>',
            reverse("admin:billing_quote_change", args=[payment.quote_id]), payment.quote,
        )

    @admin.display(description="Asked for", ordering="amount_cents")
    def asked(self, payment):
        return _money(payment.amount)

    @admin.display(description="Paid", ordering="paid_cents")
    def paid_amount(self, payment):
        return _money(payment.paid) or "-"

    @admin.display(description="Refunded", ordering="refunded_cents")
    def refunded_amount(self, payment):
        return _money(payment.refunded) if payment.refunded_cents else "-"

    @admin.display(description="Real or test")
    def kind(self, payment):
        if payment.livemode is None:
            return "-"
        return "Real payment" if payment.livemode else "Stripe TEST payment (no real money)"


@admin.register(PaymentTerms)
class PaymentTermsAdmin(TestModeNoticeMixin, AuditedSaveMixin, AngelCareModelAdmin):
    """
    Payment settings (ADR 0050): one page with the switch that offers
    online payment and the two texts the customer reads before paying.
    There is one row; it can't be added to or deleted, and every change is
    in the audit log.
    """

    audit_name = "PaymentTerms"
    list_display = ("__str__", "offered_now", "updated_at")
    fields = ("offered_now", "enabled", "what_paying_means", "refund_policy", "updated_at")
    readonly_fields = ("offered_now", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        PaymentTerms.load()  # the one settings row always exists (as Claims settings does)
        return super().changelist_view(request, extra_context)

    @admin.display(description="Right now")
    def offered_now(self, terms):
        if not payments.configured():
            return ("Online payment is OFF. Stripe is not set up on the server yet, so the switch below "
                    "has no effect until it is.")
        if terms is None or not terms.enabled:
            return "Online payment is OFF. Price emails ask the customer to call or send a message to book."
        return "Online payment is ON. Price emails carry a payment link."


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

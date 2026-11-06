from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.views.decorators.cache import never_cache

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change

from . import services, x12
from .models import Claim, ClaimFile, ClaimSettings, ClaimStatus, ResponseFile


HOW_TO_MAKE_837P = (
    "To make an 837P file: on the Claims list, tick the Draft claims, choose “Make an 837P file from the "
    "selected draft claims” in the Action menu, and click Run. Drafts come from TMHP invoices: “Create draft "
    "claims from TMHP invoices” on Claims needing attention."
)


@admin.register(Claim)
class ClaimAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    list_display = (
        "patient_control_number",
        "invoice",
        "payer",
        "amount_claimed",
        "amount_paid",
        "status",
        "deadline",
        "submitted_date",
    )
    list_filter = ("status", "payer")
    search_fields = ("claim_number", "patient_control_number", "invoice__trip__passenger__legal_name")
    autocomplete_fields = ("invoice", "payer")
    readonly_fields = ("id", "patient_control_number", "deadline", "claim_file", "replaces", "reasons",
                       "created_at", "updated_at")
    exclude = ("response_details",)
    ordering = ("-created_at",)
    actions = ["make_837p", "resubmit"]

    def changelist_view(self, request, extra_context=None):
        if request.method == "GET":
            self.message_user(request, HOW_TO_MAKE_837P, messages.INFO)
        return super().changelist_view(request, extra_context)

    @admin.display(description="Filing deadline")
    def deadline(self, claim):
        when = claim.filing_deadline
        if not when:
            return "—"
        days = (when - services.today()).days
        if claim.status in (ClaimStatus.PAID,):
            return f"{when:%b %d, %Y}"
        if days < 0:
            return format_html('<strong style="color:#b42318">{} (passed)</strong>', f"{when:%b %d, %Y}")
        if days <= services.WARN_DAYS:
            return format_html('<strong style="color:#9a5b00">{} ({} days)</strong>', f"{when:%b %d, %Y}", days)
        return f"{when:%b %d, %Y}"

    @admin.display(description="Rejection / denial reasons")
    def reasons(self, claim):
        if not claim.response_details:
            return "—"
        return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>", ((r,) for r in claim.response_details)))

    def get_urls(self):
        view = lambda fn: self.admin_site.admin_view(never_cache(fn))  # noqa: E731
        return [
            path("from-invoices/", view(self.from_invoices_view), name="claims_claim_from_invoices"),
            path("attention/", view(self.attention_view), name="claims_claim_attention"),
        ] + super().get_urls()

    def from_invoices_view(self, request):
        if request.method != "POST" or not self.has_add_permission(request):
            raise PermissionDenied
        try:
            made = services.claims_from_invoices(user=request.user)
        except x12.X12Error as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(request, f"{len(made)} draft claim{'s' if len(made) != 1 else ''} created.",
                              messages.SUCCESS if made else messages.INFO)
        return redirect("admin:claims_claim_changelist")

    def attention_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return TemplateResponse(request, "admin/claims/attention.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Claims needing attention", "rows": services.needs_attention(),
            "warn_days": services.WARN_DAYS, "can_change": self.has_change_permission(request),
        })

    @admin.action(description="Make an 837P file from the selected draft claims")
    def make_837p(self, request, queryset):
        try:
            claim_file = services.make_file(queryset.select_related("invoice__trip__passenger", "replaces"),
                                            user=request.user)
        except x12.X12Error as exc:
            for problem in exc.problems:
                self.message_user(request, problem, messages.ERROR)
            return None
        url = reverse("admin:claims_claimfile_download", args=[claim_file.pk])
        self.message_user(request, format_html(
            '837P file {} made with {} claims. <a href="{}" class="underline">Download it</a> and upload it in '
            "TexMedConnect.", f"#{claim_file.control_number:09d}", claim_file.claims.count(), url), messages.SUCCESS)
        return None

    @admin.action(description="Resubmit the selected rejected/denied claims (new drafts)")
    def resubmit(self, request, queryset):
        made = 0
        for claim in queryset:
            try:
                services.resubmit(claim, user=request.user)
                made += 1
            except x12.X12Error as exc:
                self.message_user(request, f"{claim.patient_control_number}: {exc}", messages.WARNING)
        self.message_user(request, f"{made} new draft claim{'s' if made != 1 else ''}: check them, then make a file.",
                          messages.SUCCESS if made else messages.INFO)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Claim",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )


@admin.register(ClaimSettings)
class ClaimSettingsAdmin(AngelCareModelAdmin):
    """One page: what TMHP assigned us. Files can't be made until every required field is filled."""

    autocomplete_fields = ("tmhp_payer",)
    readonly_fields = ("still_needed",)
    fieldsets = (
        (None, {"fields": ("still_needed", "tmhp_payer", "production")}),
        ("Submitter and receiver (TMHP EDI enrollment / companion guide)", {"fields": (
            "submitter_id", "submitter_contact_name", "submitter_contact_phone", "receiver_name", "receiver_id",
            "interchange_qualifier", "payer_id")}),
        ("Billing provider", {"fields": ("billing_npi", "tpi", "taxonomy_code", "billing_street", "billing_city",
                                         "billing_state", "billing_zip")}),
        ("Claim defaults", {"fields": ("place_of_service", "procedure_code", "mileage_code", "default_diagnosis_code",
                                       "default_modifier_1", "default_modifier_2")}),
    )

    @admin.display(description="Still needed")
    def still_needed(self, obj):
        missing = obj.missing() if obj else ClaimSettings().missing()
        return ", ".join(missing) if missing else "Nothing: 837P files can be made."

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_display = ("__str__", "still_needed", "production", "updated_at")

    def changelist_view(self, request, extra_context=None):
        ClaimSettings.load()  # the one settings row always exists
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_change(actor=request.user, action="changed", resource_type="ClaimSettings", resource_id=obj.pk,
                      after={name: str(form.cleaned_data.get(name)) for name in form.changed_data}, source="admin")


@admin.register(ClaimFile)
class ClaimFileAdmin(AngelCareModelAdmin):
    list_display = ("__str__", "claim_count", "production", "acknowledged", "created_by", "created_at", "download")
    readonly_fields = ("control_number", "production", "acknowledged", "created_by", "created_at", "download")
    exclude = ("content",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        """Files are made from draft claims, not added here: send people there."""
        return redirect(reverse("admin:claims_claim_changelist") + "?status__exact=draft")

    def changelist_view(self, request, extra_context=None):
        if request.method == "GET":
            self.message_user(request, HOW_TO_MAKE_837P, messages.INFO)
        return super().changelist_view(request, extra_context)

    @admin.display(description="Claims")
    def claim_count(self, claim_file):
        return claim_file.claims.count()

    @admin.display(description="File")
    def download(self, claim_file):
        return format_html('<a class="text-primary-600 underline" href="{}">Download</a>',
                           reverse("admin:claims_claimfile_download", args=[claim_file.pk]))

    def get_urls(self):
        return [path("<uuid:pk>/download/", self.admin_site.admin_view(never_cache(self.download_view)),
                     name="claims_claimfile_download")] + super().get_urls()

    def download_view(self, request, pk):
        claim_file = get_object_or_404(ClaimFile, pk=pk)
        if not self.has_view_permission(request, claim_file):
            raise PermissionDenied
        record_change(actor=request.user, action="downloaded", resource_type="ClaimFile", resource_id=claim_file.pk,
                      source="admin")
        response = HttpResponse(claim_file.content, content_type="text/plain")
        response["Content-Disposition"] = f'attachment; filename="{claim_file.filename}"'
        return response


class ResponseUploadForm(forms.Form):
    file = forms.FileField(help_text="A 999, 277CA or 835 file downloaded from TexMedConnect.")


@admin.register(ResponseFile)
class ResponseFileAdmin(AngelCareModelAdmin):
    list_display = ("kind", "original_name", "uploaded_by", "created_at")
    readonly_fields = ("kind", "original_name", "what_changed", "uploaded_by", "created_at")
    exclude = ("content", "summary")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="What it changed")
    def what_changed(self, response):
        return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>", ((line,) for line in response.summary)))

    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = ResponseUploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                text = upload.read().decode("utf-8", errors="replace")
                response = services.apply_response(text, upload.name, user=request.user)
            except x12.X12Error as exc:
                form.add_error("file", str(exc))
            else:
                self.message_user(request, f"{response.kind} read: {len(response.summary)} item(s).", messages.SUCCESS)
                return redirect("admin:claims_responsefile_change", response.pk)
        return TemplateResponse(request, "admin/claims/upload_response.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Upload a TMHP response file", "form": form,
        })

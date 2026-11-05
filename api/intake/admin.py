"""
Trip imports in the admin (ADR 0038): Operations → Trip imports.

Upload → (map the columns, first time per sender) → the import's page:
its report, and every row with its status. Ready rows are created in
one go; rows needing review open side by side (the sender's text and
ours) to fix and approve, or reject. Everything is audited, and viewing
an import or a row is logged like any PHI page.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.cache import never_cache

from accounts.admin_base import AngelCareModelAdmin
from audit.services import record_change
from payers.models import Payer
from trips.models import TripSource

from . import mapping, services
from .models import ImportBatch, ImportRow, MappingTemplate, RowStatus

INPUT = "border border-base-200 px-2 py-1 rounded-default text-sm w-full dark:bg-base-900 dark:border-base-800"

# What staff can correct on a row in review (our field names).
EDITABLE = [name for name, _ in mapping.FIELDS if name not in ("rider_first", "rider_last")]


class UploadForm(forms.Form):
    source = forms.ChoiceField(choices=TripSource.choices, help_text="Who sent this file.")
    payer = forms.ModelChoiceField(queryset=Payer.objects.order_by("name"), required=False,
                                   help_text="Who pays for these trips, if known.")
    file = forms.FileField(help_text="CSV, Excel (.xlsx) or a PDF with text (not a scan).")


@admin.register(ImportBatch)
class ImportBatchAdmin(AngelCareModelAdmin):
    list_display = ("reference", "original_name", "source", "payer", "summary", "uploaded_by", "created_at")
    list_filter = ("source", "payer")
    search_fields = ("original_name",)
    ordering = ("-created_at",)

    @admin.display(description="Import")
    def reference(self, batch):
        return batch.reference

    @admin.display(description="Rows")
    def summary(self, batch):
        c = batch.counts()
        return f"{c['created']} created · {c['review']} to review · {c['ready']} ready · {c['duplicate']} duplicates"

    def has_delete_permission(self, request, obj=None):
        return False  # the original file is the record of what was received

    def get_urls(self):
        view = lambda fn: self.admin_site.admin_view(never_cache(fn))  # noqa: E731
        return [
            path("upload/", view(self.upload_view), name="intake_importbatch_upload"),
            path("<uuid:pk>/mapping/", view(self.mapping_view), name="intake_importbatch_mapping"),
            path("<uuid:pk>/create-ready/", view(self.create_ready_view), name="intake_importbatch_create_ready"),
            path("<uuid:pk>/file/", view(self.file_view), name="intake_importbatch_file"),
            path("row/<uuid:pk>/", view(self.row_view), name="intake_importrow_review"),
        ] + super().get_urls()

    def _context(self, request, **extra):
        return {**self.admin_site.each_context(request), "opts": self.model._meta, "input_class": INPUT, **extra}

    def add_view(self, request, form_url="", extra_context=None):
        return self.upload_view(request)

    def upload_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = UploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                batch = services.upload(
                    form.cleaned_data["file"], source=form.cleaned_data["source"],
                    payer=form.cleaned_data["payer"], user=request.user,
                )
            except services.UploadError as exc:
                form.add_error("file", str(exc))
            else:
                if batch.template:
                    self.message_user(request, f"Read {batch.rows.count()} rows using the saved mapping "
                                               f"“{batch.template.name}”.", messages.SUCCESS)
                    return redirect("admin:intake_importbatch_change", batch.pk)
                return redirect("admin:intake_importbatch_mapping", batch.pk)
        return TemplateResponse(request, "admin/intake/upload.html", self._context(
            request, title="Import trips from a file", form=form))

    def mapping_view(self, request, pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        if not self.has_change_permission(request, batch):
            raise PermissionDenied
        if request.method == "POST":
            chosen = {f: request.POST.get(f, "") for f in mapping.FIELD_NAMES}
            batch.field_map = {f: c for f, c in chosen.items() if c in batch.headers}
            name = request.POST.get("template_name", "").strip()
            if name:
                template = batch.template if batch.template and batch.template.name == name else MappingTemplate(
                    source=batch.source, payer=batch.payer, created_by=request.user)
                template.name, template.headers, template.field_map = name, batch.headers, batch.field_map
                template.save()
                batch.template = template
            batch.save()
            services.apply_mapping(batch)
            record_change(actor=request.user, action="mapped", resource_type="ImportBatch", resource_id=batch.pk,
                          after={"field_map": batch.field_map, "template": name or None}, source="admin")
            return redirect("admin:intake_importbatch_change", batch.pk)
        sample = batch.rows.order_by("number").first()
        fields = [
            {"name": f, "label": label, "chosen": batch.field_map.get(f, ""),
             "example": sample.raw.get(batch.field_map.get(f, ""), "") if sample else ""}
            for f, label in mapping.FIELDS
        ]
        return TemplateResponse(request, "admin/intake/mapping.html", self._context(
            request, title=f"Match the columns — {batch.original_name}", batch=batch, fields=fields,
            sample=sample, template_name=batch.template.name if batch.template else ""))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        batch = get_object_or_404(ImportBatch, pk=object_id)
        if not self.has_view_permission(request, batch):
            raise PermissionDenied
        record_change(actor=request.user, action="viewed", resource_type="ImportBatch", resource_id=batch.pk,
                      source="admin")
        status = request.GET.get("status", "")
        rows = batch.rows.select_related("trip", "return_trip").order_by("number")
        if status in RowStatus.values:
            rows = rows.filter(status=status)
        counts = batch.counts()
        return TemplateResponse(request, "admin/intake/batch.html", self._context(
            request, title=f"Import {batch.reference} — {batch.original_name}", batch=batch, rows=rows,
            counts=counts, status=status, statuses=RowStatus.choices,
            can_change=self.has_change_permission(request, batch), mapped=bool(batch.field_map)))

    def create_ready_view(self, request, pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        if request.method != "POST" or not self.has_change_permission(request, batch):
            raise PermissionDenied
        made, failures = services.create_ready(batch, user=request.user)
        self.message_user(request, f"{made} row{'s' if made != 1 else ''} created as trips.", messages.SUCCESS)
        for failure in failures:
            self.message_user(request, failure, messages.WARNING)
        return redirect("admin:intake_importbatch_change", batch.pk)

    def file_view(self, request, pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        if not self.has_view_permission(request, batch):
            raise PermissionDenied
        record_change(actor=request.user, action="downloaded", resource_type="ImportBatch", resource_id=batch.pk,
                      source="admin")
        return FileResponse(batch.file.open("rb"), as_attachment=True, filename=batch.original_name)

    def row_view(self, request, pk):
        row = get_object_or_404(ImportRow.objects.select_related("batch", "passenger", "trip", "return_trip"), pk=pk)
        if not self.has_view_permission(request, row.batch):
            raise PermissionDenied
        can_change = self.has_change_permission(request, row.batch)
        open_row = row.status in (RowStatus.READY, RowStatus.REVIEW, RowStatus.FAILED)
        if request.method == "POST":
            if not can_change or not open_row:
                raise PermissionDenied
            action = request.POST.get("action")
            if action == "reject":
                services.reject(row, user=request.user)
                self.message_user(request, f"Row {row.number} rejected: no trip made.", messages.INFO)
                return redirect("admin:intake_importbatch_change", row.batch_id)
            row.values = {f: request.POST.get(f, "").strip() for f in EDITABLE}
            services.recheck(row)
            record_change(actor=request.user, action="edited", resource_type="ImportRow", resource_id=row.pk,
                          after={"batch": str(row.batch_id), "row": row.number}, source="admin")
            if action == "approve" and row.status in (RowStatus.READY, RowStatus.REVIEW):
                try:
                    services.create_trips(row, user=request.user)
                except ValidationError as exc:
                    self.message_user(request, "Not created: " + "; ".join(exc.messages), messages.ERROR)
                else:
                    self.message_user(request, f"Row {row.number}: trip created.", messages.SUCCESS)
                    return redirect("admin:intake_importbatch_change", row.batch_id)
            row.refresh_from_db()
        else:
            record_change(actor=request.user, action="viewed", resource_type="ImportRow", resource_id=row.pk,
                          source="admin")
        labels = dict(mapping.FIELDS)
        fields = [{"name": f, "label": labels[f], "value": row.values.get(f, "")} for f in EDITABLE]
        return TemplateResponse(request, "admin/intake/row.html", self._context(
            request, title=f"Row {row.number} — {row.batch.original_name}", row=row, fields=fields,
            raw=list(row.raw.items()), can_change=can_change and open_row,
            batch_url=reverse("admin:intake_importbatch_change", args=[row.batch_id])))


@admin.register(MappingTemplate)
class MappingTemplateAdmin(AngelCareModelAdmin):
    """Saved column mappings: one per sender layout. Rename or delete here; edit through an import."""

    list_display = ("name", "source", "payer", "updated_at")
    list_filter = ("source",)
    search_fields = ("name",)
    fields = ("name", "source", "payer", "mapped_columns")
    readonly_fields = ("mapped_columns",)

    def has_add_permission(self, request):
        return False

    @admin.display(description="Columns")
    def mapped_columns(self, template):
        labels = dict(mapping.FIELDS)
        return "; ".join(f"{labels.get(f, f)} ← {c}" for f, c in template.field_map.items()) or "—"

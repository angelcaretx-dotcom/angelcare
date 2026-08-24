from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils import timezone

from audit.services import record_change

from .models import Document, DocumentStatus


class DocumentInline(GenericTabularInline):
    """
    Attach to any parent ModelAdmin (see DriverAdmin, VehicleAdmin) so
    staff can upload a document directly from that record's page.
    Verification/rejection happens in the standalone Document admin
    below, not here -- so the audit-logged status-change logic lives
    in exactly one place regardless of where a document was uploaded.

    Setting `uploaded_by` on newly-created Documents happens in
    DocumentUploaderAdminMixin.save_formset below, not here --
    InlineModelAdmin has no save-time hook of its own; the parent
    ModelAdmin (DriverAdmin/VehicleAdmin) is what actually saves the
    inline's formset.
    """

    model = Document
    extra = 0
    fields = ("document_type", "file", "expiration_date", "status", "notes")
    readonly_fields = ("status",)


class DocumentUploaderAdminMixin:
    """
    Mix into any ModelAdmin that includes DocumentInline, so uploads
    made from that page record who uploaded them.
    """

    def save_formset(self, request, form, formset, change):
        if formset.model is Document:
            instances = formset.save(commit=False)
            # Document.id is a UUID assigned at instantiation, not at
            # save time, so `instance.pk is None` can't detect "new"
            # here -- formset.new_objects (populated by the save(
            # commit=False) call above) is the reliable signal.
            new_instances = set(formset.new_objects)
            for instance in instances:
                if instance in new_instances:
                    instance.uploaded_by = request.user
                instance.save()
            formset.save_m2m()
        else:
            super().save_formset(request, form, formset, change)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "document_type",
        "content_object",
        "status",
        "expiration_date",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "document_type")
    readonly_fields = (
        "id",
        "content_type",
        "object_id",
        "uploaded_by",
        "verified_by",
        "verified_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None

        if not change:
            obj.uploaded_by = request.user

        if obj.status == DocumentStatus.VERIFIED and previous_status != DocumentStatus.VERIFIED:
            obj.verified_by = request.user
            obj.verified_at = timezone.now()

        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Document",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

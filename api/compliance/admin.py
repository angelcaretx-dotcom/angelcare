from django.contrib import admin

from audit.services import record_change
from documents.admin import DocumentInline, DocumentUploaderAdminMixin

from .models import ComplianceRecord


@admin.register(ComplianceRecord)
class ComplianceRecordAdmin(DocumentUploaderAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "record_type",
        "issuing_authority",
        "expiration_date",
        "status",
        "created_at",
    )
    list_filter = ("status", "record_type")
    search_fields = ("name", "issuing_authority", "reference_number")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("name",)
    inlines = [DocumentInline]

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="ComplianceRecord",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

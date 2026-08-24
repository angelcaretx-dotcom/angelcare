from django.contrib import admin

from audit.services import record_change
from documents.admin import DocumentInline, DocumentUploaderAdminMixin

from .models import Driver


@admin.register(Driver)
class DriverAdmin(DocumentUploaderAdminMixin, admin.ModelAdmin):
    list_display = (
        "legal_name",
        "phone",
        "employment_type",
        "license_expiration_date",
        "status",
        "created_at",
    )
    list_filter = ("status", "employment_type")
    search_fields = ("legal_name", "phone", "email", "license_number")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("legal_name",)
    inlines = [DocumentInline]

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Driver",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

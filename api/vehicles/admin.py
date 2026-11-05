from django.contrib import admin, messages

from accounts.admin_base import AngelCareModelAdmin
from audit.services import record_change
from documents.admin import DocumentInline, DocumentUploaderAdminMixin

from .checks import VehicleCheckError, clear
from .models import Vehicle, VehicleCheck


@admin.register(Vehicle)
class VehicleAdmin(DocumentUploaderAdminMixin, AngelCareModelAdmin):
    list_display = (
        "__str__",
        "vin",
        "wheelchair_capable",
        "stretcher_capable",
        "passenger_capacity",
        "status",
        "created_at",
    )
    list_filter = ("status", "wheelchair_capable", "stretcher_capable")
    search_fields = ("vin", "make", "model", "license_plate")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("make", "model")
    inlines = [DocumentInline]

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Vehicle",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )


@admin.register(VehicleCheck)
class VehicleCheckAdmin(AngelCareModelAdmin):
    """
    Daily vehicle checks from the driver app (ADR 0034). Proof that the
    vehicle was inspected, so it's read-only; the one thing staff do here
    is mark a failed check fixed, which puts the vehicle back in service.
    """

    list_display = ("service_date", "vehicle", "driver", "result", "start_odometer", "end_odometer", "miles")
    list_filter = ("service_date", "vehicle")
    search_fields = ("vehicle__license_plate", "vehicle__vin", "driver__legal_name")
    ordering = ("-service_date",)
    date_hierarchy = "service_date"
    fields = (
        "vehicle",
        "service_date",
        "driver",
        "checked_at",
        "result",
        "problems",
        "notes",
        "start_odometer",
        "end_odometer",
        "miles",
        "ended_at",
        "ended_by",
        "cleared_note",
        "cleared_by",
        "cleared_at",
    )

    def get_readonly_fields(self, request, obj=None):
        locked = [f for f in self.fields if f != "cleared_note"]
        if obj is None or not obj.blocks_vehicle or not self.has_change_permission(request, obj):
            locked.append("cleared_note")
        return locked

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Result")
    def result(self, obj):
        if obj.passed:
            return "Passed"
        return "Failed — out of service" if obj.blocks_vehicle else "Failed — fixed"

    @admin.display(description="Problems")
    def problems(self, obj):
        return "; ".join(obj.failed_labels()) or "—"

    @admin.display(description="Miles")
    def miles(self, obj):
        return obj.miles if obj.miles is not None else "—"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "cleared_note" in form.base_fields:
            form.base_fields["cleared_note"].help_text = (
                "Fixed? Say what was done and save: the vehicle goes back in service."
            )
        return form

    def save_model(self, request, obj, form, change):
        note = form.cleaned_data.get("cleared_note", "")
        if "cleared_note" in form.changed_data and note.strip():
            try:
                clear(obj, note=note, user=request.user)
            except VehicleCheckError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(request, f"{obj.vehicle} is back in service.", level=messages.SUCCESS)

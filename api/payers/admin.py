from django import forms
from django.contrib import admin
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget

from accounts.admin_base import AngelCareModelAdmin
from audit.services import record_change

from drivers.models import CredentialType

from .models import Payer


class PayerForm(forms.ModelForm):
    required_credentials = forms.MultipleChoiceField(
        choices=CredentialType.choices,
        required=False,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="Driver credentials this payer requires on its trips, on top of the ones every "
        "driver needs (ADR 0036). A driver missing one can't be assigned this payer's trips.",
    )

    class Meta:
        model = Payer
        fields = "__all__"


@admin.register(Payer)
class PayerAdmin(AngelCareModelAdmin):
    form = PayerForm
    list_display = (
        "name",
        "payer_type",
        "contact_name",
        "phone",
        "status",
        "created_at",
    )
    list_filter = ("status", "payer_type")
    search_fields = ("name", "contact_name", "phone", "email")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("name",)

    def save_model(self, request, obj, form, change):
        previous_status = form.initial.get("status") if change else None
        super().save_model(request, obj, form, change)

        if change and previous_status is not None and previous_status != obj.status:
            record_change(
                actor=request.user,
                action="status_changed",
                resource_type="Payer",
                resource_id=obj.id,
                before={"status": previous_status},
                after={"status": obj.status},
                source="admin",
            )

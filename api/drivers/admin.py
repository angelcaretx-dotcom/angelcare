from datetime import date

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.views.decorators.cache import never_cache
from unfold.admin import TabularInline

from accounts.admin_base import AngelCareModelAdmin
from audit.services import record_change
from documents.admin import DocumentInline, DocumentUploaderAdminMixin

from .models import (
    REQUIRED_FOR_EVERY_TRIP,
    CredentialType,
    Driver,
    DriverCredential,
    current_credentials,
)


class DriverCredentialInline(TabularInline):
    """Credentials the driver holds (ADR 0036). Renewing? Add a new row: the newest counts."""

    model = DriverCredential
    extra = 0
    fields = ("credential_type", "completed_on", "expires_on", "reference", "notes")
    verbose_name_plural = "Credentials (renewing? add a new row: the newest of each kind counts)"


@admin.register(Driver)
class DriverAdmin(DocumentUploaderAdminMixin, AngelCareModelAdmin):
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
    readonly_fields = ("id", "credential_status", "app_login", "created_at", "updated_at")
    ordering = ("legal_name",)
    inlines = [DriverCredentialInline, DocumentInline]

    @admin.display(description="Credentials check")
    def credential_status(self, obj):
        """What blocks assignment today (ADR 0036)."""
        if obj is None or obj.pk is None:
            return "Save the driver, then add their credentials below."
        today = date.today()
        current = current_credentials(obj)
        kinds = list(REQUIRED_FOR_EVERY_TRIP) + [CredentialType.WHEELCHAIR_SECUREMENT]
        rows = []
        for kind in kinds:
            credential = current.get(kind)
            label = CredentialType(kind).label
            if kind == CredentialType.WHEELCHAIR_SECUREMENT:
                label += " (wheelchair/stretcher trips)"
            if credential is None:
                state, colour = "missing", "#b42318"
            elif not credential.is_valid_on(today):
                state, colour = f"expired {credential.expires_on:%b %d, %Y}", "#b42318"
            elif credential.expires_on:
                state, colour = f"OK until {credential.expires_on:%b %d, %Y}", "#1f7a4d"
            else:
                state, colour = "OK (doesn't expire)", "#1f7a4d"
            rows.append((label, colour, state))
        return format_html(
            "<ul>{}</ul><p>Missing or expired items stop this driver being assigned to trips. "
            "Some payers require more (see the payer's page).</p>",
            format_html_join("", '<li>{}: <strong style="color:{}">{}</strong></li>', rows),
        )

    def save_formset(self, request, form, formset, change):
        if formset.model is not DriverCredential:
            return super().save_formset(request, form, formset, change)
        instances = formset.save()
        for credential in instances:
            record_change(
                actor=request.user,
                action="credential_saved",
                resource_type="Driver",
                resource_id=form.instance.pk,
                after={
                    "credential": credential.credential_type,
                    "completed_on": str(credential.completed_on),
                    "expires_on": str(credential.expires_on) if credential.expires_on else None,
                },
                source="admin",
            )
        for credential in formset.deleted_objects:
            record_change(
                actor=request.user,
                action="credential_deleted",
                resource_type="Driver",
                resource_id=form.instance.pk,
                before={"credential": credential.credential_type, "expires_on": str(credential.expires_on)},
                source="admin",
            )

    @admin.display(description="Driver app login")
    def app_login(self, obj):
        if obj is None or obj.pk is None:
            return "Save the driver first."
        url = reverse("admin:drivers_driver_app_login", args=[obj.pk])
        if obj.user_id:
            return format_html(
                'Set up — signs in with their email or phone. <a href="{}" class="text-primary-600 underline">Reset password</a>',
                url,
            )
        return format_html('Not set up. <a href="{}" class="text-primary-600 underline">Create app login</a>', url)

    def get_urls(self):
        return [
            path(
                "<uuid:driver_id>/app-login/",
                self.admin_site.admin_view(never_cache(self.app_login_view)),
                name="drivers_driver_app_login",
            )
        ] + super().get_urls()

    def app_login_view(self, request, driver_id):
        """ADR 0029: create or reset a driver's app login; handover shown once."""
        from driver_app.access import DriverAccessError, setup_driver_login

        driver = get_object_or_404(Driver, pk=driver_id)
        if not self.has_change_permission(request, driver):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            "driver": driver,
            "login_url": f"{settings.SITE_URL}/driver/",
            "title": f"Driver app login — {driver.legal_name}",
        }
        if request.method == "POST":
            try:
                password = setup_driver_login(driver, actor=request.user)
            except DriverAccessError as exc:
                messages.error(request, str(exc))
                return redirect("admin:drivers_driver_change", driver.pk)
            context.update(password=password, title=f"Handover sheet — {driver.legal_name}")
            response = TemplateResponse(request, "admin/drivers/app_login_handover.html", context)
            response["Cache-Control"] = "no-store"  # the password must never be cached
            return response
        return TemplateResponse(request, "admin/drivers/app_login.html", context)

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

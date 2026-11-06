import csv
import io
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.cache import never_cache
from unfold.admin import StackedInline, TabularInline

from accounts.admin_base import AngelCareModelAdmin
from audit.admin_mixins import AccessLoggedAdminMixin
from audit.services import record_change
from trips.standing import texas_today

from . import services
from .models import Facility, FacilityActivity, FacilityContact, FacilityKind, FacilityUser, Stage

RECENT_TRIPS = 10


class FacilityContactInline(TabularInline):
    model = FacilityContact
    extra = 0
    fields = ("name", "title", "phone", "email", "is_primary", "notes")


class FacilityActivityInline(StackedInline):
    """New calls and visits are added here; past ones are a record and aren't edited."""

    model = FacilityActivity
    extra = 1
    fields = ("kind", "text")
    verbose_name = "log a call, visit or note"
    verbose_name_plural = "log a call, visit or note"

    def get_queryset(self, request):
        return super().get_queryset(request).none()  # history is shown read-only above

    def has_change_permission(self, request, obj=None):
        return False


class PortalUserInline(TabularInline):
    model = FacilityUser
    extra = 0
    can_delete = False
    fields = ("full_name", "email", "title", "signed_up", "active")
    readonly_fields = fields
    verbose_name_plural = "portal users (add them under Facility portal users)"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Set up")
    def signed_up(self, portal_user):
        return portal_user.verified_at.strftime("%b %d, %Y") if portal_user.verified_at else "Invite not used yet"

    @admin.display(description="Active", boolean=True)
    def active(self, portal_user):
        return portal_user.user.is_active


class FollowUpFilter(admin.SimpleListFilter):
    title = "follow-up"
    parameter_name = "follow_up"

    def lookups(self, request, model_admin):
        return (("due", "Due today or overdue"), ("week", "Due in the next 7 days"), ("none", "No follow-up set"))

    def queryset(self, request, queryset):
        today = texas_today()
        if self.value() == "due":
            return queryset.filter(next_follow_up__lte=today).exclude(stage=Stage.LOST)
        if self.value() == "week":
            return queryset.filter(next_follow_up__gt=today, next_follow_up__lte=today + timedelta(days=7))
        if self.value() == "none":
            return queryset.filter(next_follow_up__isnull=True).exclude(stage=Stage.LOST)
        return queryset


class ImportForm(forms.Form):
    file = forms.FileField(help_text="A CSV file with a header row.")


IMPORT_COLUMNS = {
    "name": ("name", "facility", "facility name"),
    "kind": ("kind", "type", "facility type"),
    "street": ("street", "address", "street address"),
    "city": ("city",),
    "state": ("state",),
    "zip": ("zip", "zip code", "postal code"),
    "phone": ("phone", "phone number", "main phone"),
    "email": ("email", "e-mail"),
    "website": ("website", "web site", "url"),
    "notes": ("notes", "note", "comments"),
    "contact_name": ("contact", "contact name"),
    "contact_title": ("contact title", "title", "role"),
    "contact_phone": ("contact phone",),
    "contact_email": ("contact email",),
}
KIND_WORDS = {
    FacilityKind.HOSPITAL: ("hospital",),
    FacilityKind.NURSING_HOME: ("nursing", "skilled", "snf"),
    FacilityKind.ASSISTED_LIVING: ("assisted",),
    FacilityKind.DIALYSIS: ("dialysis",),
    FacilityKind.REHAB: ("rehab",),
    FacilityKind.CLINIC: ("clinic", "doctor", "office", "physician"),
}


def _kind(text: str) -> str:
    text = (text or "").lower()
    for kind, words in KIND_WORDS.items():
        if any(word in text for word in words):
            return kind
    return FacilityKind.OTHER


def import_facilities(text: str, *, user) -> tuple[list[Facility], list[str]]:
    """New leads from a CSV. A facility already on file (same name and city) is skipped, never overwritten."""
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
    column = {field: next((headers[n] for n in names if n in headers), None) for field, names in IMPORT_COLUMNS.items()}
    if column["name"] is None:
        return [], ["No “name” (or “facility”) column found in the header row."]
    made, skipped = [], []
    with transaction.atomic():
        for number, row in enumerate(reader, start=2):
            value = {field: (row.get(col) or "").strip() if col else "" for field, col in column.items()}
            if not value["name"]:
                continue
            if Facility.objects.filter(name__iexact=value["name"], city__iexact=value["city"]).exists():
                skipped.append(f"Row {number}: {value['name']} is already on file.")
                continue
            facility = Facility(
                name=value["name"], kind=_kind(value["kind"] or value["name"]), street=value["street"],
                city=value["city"], state=(value["state"] or "TX")[:2].upper(), zip=value["zip"][:10],
                phone=value["phone"][:20], email=value["email"], website=value["website"], notes=value["notes"],
            )
            try:
                facility.full_clean()
            except forms.ValidationError as exc:
                skipped.append(f"Row {number}: {value['name']}: {'; '.join(exc.messages)}")
                continue
            facility.save()
            if value["contact_name"]:
                FacilityContact.objects.create(facility=facility, name=value["contact_name"],
                                               title=value["contact_title"][:100], phone=value["contact_phone"][:20],
                                               email=value["contact_email"], is_primary=True)
            made.append(facility)
        record_change(actor=user, action="imported", resource_type="Facility", resource_id=f"{len(made)} new",
                      after={"skipped": len(skipped)}, source="admin")
    return made, skipped


@admin.register(Facility)
class FacilityAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """Facilities as sales leads and accounts (ADR 0042)."""

    list_display = ("name", "kind", "stage", "city", "phone", "follow_up", "account_manager", "portal_user_count")
    list_filter = ("stage", "kind", FollowUpFilter, "account_manager")
    search_fields = ("name", "city", "phone", "contacts__name", "contacts__email")
    autocomplete_fields = ("payer",)
    readonly_fields = ("activity_history", "recent_trips", "created_at", "updated_at")
    inlines = [FacilityContactInline, FacilityActivityInline, PortalUserInline]
    ordering = ("name",)
    fieldsets = (
        (None, {"fields": ("name", "kind", "stage", "account_manager")}),
        ("Follow-up", {"fields": ("next_follow_up", "follow_up_note"),
                       "description": "Due follow-ups show on the admin home page and in the office's morning email."}),
        ("Address and phone", {"fields": ("street", "unit", "city", "state", "zip", "phone", "email", "website")}),
        ("Billing", {"fields": ("payer",)}),
        ("Notes", {"fields": ("notes",)}),
        ("History", {"fields": ("activity_history", "recent_trips")}),
        ("Record", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("account_manager").prefetch_related("portal_users")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "account_manager":
            from django.contrib.auth import get_user_model

            kwargs["queryset"] = get_user_model().objects.filter(is_staff=True, is_active=True).order_by("username")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Next follow-up", ordering="next_follow_up")
    def follow_up(self, facility):
        if not facility.next_follow_up:
            return "—"
        text = f"{facility.next_follow_up:%b %d, %Y}"
        if facility.stage != Stage.LOST and facility.next_follow_up <= texas_today():
            return format_html('<strong style="color:#b42318">{} (due)</strong>', text)
        return text

    @admin.display(description="Portal users")
    def portal_user_count(self, facility):
        return len(facility.portal_users.all())

    @admin.display(description="Calls, visits and notes")
    def activity_history(self, facility):
        items = facility.activities.select_related("author")[:50] if facility.pk else []
        if not items:
            return "Nothing logged yet."
        return format_html("<ul>{}</ul>", format_html_join("", "<li><strong>{} {}</strong> ({}): {}</li>", (
            (a.get_kind_display(), timezone.localtime(a.created_at).strftime("%b %d, %Y %I:%M %p"),
             a.author.get_username() if a.author_id else "—", a.text) for a in items)))

    @admin.display(description=f"Latest {RECENT_TRIPS} trips")
    def recent_trips(self, facility):
        if not facility.pk:
            return "—"
        trips = facility.trips.select_related("passenger").order_by("-scheduled_pickup_datetime")[:RECENT_TRIPS]
        if not trips:
            return "No trips linked yet."
        return format_html("<ul>{}</ul>", format_html_join("", '<li><a class="underline" href="{}">{}</a> {} ({})</li>', (
            (reverse("admin:trips_trip_change", args=[t.pk]),
             timezone.localtime(t.scheduled_pickup_datetime).strftime("%b %d, %Y %I:%M %p"),
             t.passenger.legal_name if t.passenger_id else "", t.get_status_display()) for t in trips)))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_change(actor=request.user, action="changed" if change else "created", resource_type="Facility",
                      resource_id=obj.pk, after={name: str(form.cleaned_data.get(name)) for name in form.changed_data},
                      source="admin")

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, FacilityActivity) and instance._state.adding:
                instance.author = request.user
            instance.save()
        for instance in formset.deleted_objects:
            instance.delete()
        formset.save_m2m()

    def get_urls(self):
        return [path("import/", self.admin_site.admin_view(never_cache(self.import_view)),
                     name="facilities_facility_import")] + super().get_urls()

    def import_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = ImportForm(request.POST or None, request.FILES or None)
        made = skipped = None
        if request.method == "POST" and form.is_valid():
            text = form.cleaned_data["file"].read().decode("utf-8-sig", errors="replace")
            made, skipped = import_facilities(text, user=request.user)
            self.message_user(request, f"{len(made)} facilit{'ies' if len(made) != 1 else 'y'} added as leads.",
                              messages.SUCCESS if made else messages.INFO)
        return TemplateResponse(request, "admin/facilities/import.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Import facilities (CSV)", "form": form, "made": made, "skipped": skipped,
            "columns": ", ".join(names[0] for names in IMPORT_COLUMNS.values()),
        })


class FacilityUserAddForm(forms.ModelForm):
    class Meta:
        model = FacilityUser
        fields = ("facility", "full_name", "email", "title", "phone")

    def clean_email(self):
        email = services.normalize_email(self.cleaned_data["email"])
        if FacilityUser.objects.filter(email=email).exists():
            raise forms.ValidationError("That email already has a facility portal account.")
        return email


@admin.register(FacilityUser)
class FacilityUserAdmin(AccessLoggedAdminMixin, AngelCareModelAdmin):
    """
    Facility portal logins (ADR 0042). The office adds one and the person
    gets a set-up link by email; they choose their own password. Switch
    one off when someone leaves the facility.
    """

    list_display = ("full_name", "facility", "email", "verified_at", "invited_at", "is_active")
    list_filter = ("facility", ("verified_at", admin.EmptyFieldListFilter))
    search_fields = ("full_name", "email", "facility__name")
    autocomplete_fields = ("facility",)
    readonly_fields = ("email", "invited_at", "verified_at", "created_at", "updated_at")
    actions = ["resend_invite", "switch_off", "switch_on"]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = FacilityUserAddForm
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else self.readonly_fields

    def get_fields(self, request, obj=None):
        if obj is None:
            return FacilityUserAddForm.Meta.fields
        return ("facility", "full_name", "title", "phone", *self.readonly_fields)

    @admin.display(description="Active", boolean=True)
    def is_active(self, portal_user):
        return portal_user.user.is_active

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            record_change(actor=request.user, action="changed", resource_type="FacilityUser", resource_id=obj.pk,
                          after={name: str(form.cleaned_data.get(name)) for name in form.changed_data},
                          source="admin")
            return
        services.create_portal_user(obj, actor=request.user)

    def response_add(self, request, obj, post_url_continue=None):
        self.message_user(request, f"Set-up link emailed to {obj.email}. It works for 3 days.", messages.SUCCESS)
        return redirect("admin:facilities_facilityuser_changelist")

    @admin.action(description="Resend the set-up link (not yet used)", permissions=["change"])
    def resend_invite(self, request, queryset):
        pending = [u for u in queryset.select_related("user") if u.verified_at is None and u.user.is_active]
        for portal_user in pending:
            services.send_invite(portal_user)
        self.message_user(request, f"Set-up link re-sent to {len(pending)} user(s).", messages.INFO)

    def _set_active(self, request, queryset, active: bool):
        for portal_user in queryset.select_related("user"):
            if portal_user.user.is_active != active:
                portal_user.user.is_active = active
                portal_user.user.save(update_fields=["is_active"])
                record_change(actor=request.user, action="switched_on" if active else "switched_off",
                              resource_type="FacilityUser", resource_id=portal_user.pk, source="admin")

    @admin.action(description="Switch off (they can't sign in)", permissions=["change"])
    def switch_off(self, request, queryset):
        self._set_active(request, queryset, False)
        self.message_user(request, "Switched off. Anyone signed in is signed out on their next click.", messages.INFO)

    @admin.action(description="Switch back on", permissions=["change"])
    def switch_on(self, request, queryset):
        self._set_active(request, queryset, True)

from django.apps import apps
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from accounts.admin_base import AngelCareModelAdmin
from audit.services import record_change

from . import services
from .models import TrashEntry


class RecordTypeFilter(admin.SimpleListFilter):
    title = "record type"
    parameter_name = "type"

    def lookups(self, request, model_admin):
        labels = TrashEntry.objects.order_by().values_list("model_label", flat=True).distinct()
        return sorted(((label, TrashEntry(model_label=label).type_name) for label in labels), key=lambda x: x[1])

    def queryset(self, request, queryset):
        return queryset.filter(model_label=self.value()) if self.value() else queryset


def _field_rows(record: dict) -> list[tuple[str, str]]:
    """One saved row as (label, value) pairs, links shown by name when the linked record still exists."""
    try:
        model = apps.get_model(record["model"])
    except LookupError:
        return [(name, str(value)) for name, value in record["fields"].items()]
    rows = [("ID", str(record.get("pk")))]
    for name, value in record["fields"].items():
        try:
            field = model._meta.get_field(name)
        except Exception:  # noqa: BLE001 - a field since removed
            rows.append((name, str(value)))
            continue
        shown = value
        if field.is_relation and value and not field.many_to_many:
            target = field.related_model._base_manager.filter(pk=value).first()
            shown = str(target) if target else f"{value} (not on file)"
        elif field.choices and value not in (None, ""):
            shown = dict(field.flatchoices).get(value, value)
        if shown in (None, "", [], {}):
            shown = "—"
        if name in ("signature", "content") and isinstance(shown, str) and len(shown) > 80:
            shown = f"({len(shown)} characters kept)"
        rows.append((str(field.verbose_name).capitalize(), str(shown)))
    return rows


@admin.register(TrashEntry)
class TrashAdmin(AngelCareModelAdmin):
    """
    The trash bin (ADR 0046): superusers and Administrators only. Open an
    item to see the record as it was, then restore it or delete it for good.
    """

    list_display = ("label", "type_name", "included", "deleted_by_name", "deleted_from", "deleted_at")
    list_display_links = ("label",)
    list_filter = (RecordTypeFilter, "deleted_from", "deleted_at", ("deleted_by", admin.RelatedOnlyFieldListFilter))
    search_fields = ("label", "deleted_by_name")
    search_help_text = "Record name (e.g. TEST) or who deleted it."
    date_hierarchy = "deleted_at"
    ordering = ("-deleted_at",)
    actions = ["restore_selected", "purge_selected"]

    # --- Who may see it at all ------------------------------------------
    def has_module_permission(self, request):
        return services.can_use_trash(request.user)

    def has_view_permission(self, request, obj=None):
        return services.can_use_trash(request.user)

    def has_trash_permission(self, request):
        return services.can_use_trash(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Type", ordering="model_label")
    def type_name(self, entry):
        return entry.type_name

    @admin.display(description="Taken with it")
    def included(self, entry):
        extra = sum(entry.contents.values()) - 1 if entry.contents else 0
        return f"{extra} more record{'s' if extra != 1 else ''}" if extra > 0 else "—"

    # --- One item --------------------------------------------------------
    def get_urls(self):
        view = lambda fn: self.admin_site.admin_view(never_cache(fn))  # noqa: E731
        return [
            path("<uuid:pk>/restore/", view(self.restore_view), name="trash_trashentry_restore"),
            path("<uuid:pk>/purge/", view(self.purge_view), name="trash_trashentry_purge"),
        ] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if not services.can_use_trash(request.user):
            raise PermissionDenied
        entry = get_object_or_404(TrashEntry, pk=object_id)
        record_change(actor=request.user, action="viewed", resource_type="TrashEntry", resource_id=entry.pk,
                      after={"record": entry.model_label, "id": entry.object_id}, source="admin")
        main, others = entry.records[0], entry.records[1:]
        return TemplateResponse(request, "admin/trash/entry.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "title": str(entry),
            "entry": entry, "rows": _field_rows(main),
            "others": [(TrashEntry(model_label=r["model"]).type_name, r.get("pk"), _label(r)) for r in others],
            "still_pointing": services.still_pointing(entry),
        })

    def restore_view(self, request, pk):
        if request.method != "POST" or not services.can_use_trash(request.user):
            raise PermissionDenied
        entry = get_object_or_404(TrashEntry, pk=pk)
        try:
            services.restore(entry, user=request.user)
        except services.TrashError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return redirect("admin:trash_trashentry_change", entry.pk)
        self.message_user(request, f"Restored: {entry.label}.", messages.SUCCESS)
        return redirect("admin:trash_trashentry_changelist")

    def purge_view(self, request, pk):
        if not services.can_use_trash(request.user):
            raise PermissionDenied
        entry = get_object_or_404(TrashEntry, pk=pk)
        if request.method == "POST" and request.POST.get("confirm") == "yes":
            services.purge(entry, user=request.user)
            self.message_user(request, f"Permanently deleted: {entry.label}.", messages.SUCCESS)
            return redirect("admin:trash_trashentry_changelist")
        return self._confirm(request, [entry])

    def _confirm(self, request, entries, *, bulk=False):
        return TemplateResponse(request, "admin/trash/confirm_purge.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Delete for good?", "entries": [(e, services.still_pointing(e)) for e in entries],
            "bulk": bulk, "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        })

    # --- Many at once ----------------------------------------------------
    @admin.action(description="Restore the selected items", permissions=["trash"])
    def restore_selected(self, request, queryset):
        done, failed = 0, []
        for entry in queryset:
            try:
                services.restore(entry, user=request.user)
                done += 1
            except services.TrashError as exc:
                failed.append(f"{entry.label}: {exc}")
        self.message_user(request, f"{done} restored.", messages.SUCCESS if done else messages.INFO)
        for line in failed:
            self.message_user(request, line, messages.ERROR)

    @admin.action(description="Delete the selected items for good…", permissions=["trash"])
    def purge_selected(self, request, queryset):
        if request.POST.get("confirm") != "yes":
            return self._confirm(request, list(queryset), bulk=True)
        count = 0
        for entry in queryset:
            services.purge(entry, user=request.user)
            count += 1
        self.message_user(request, f"{count} permanently deleted.", messages.SUCCESS)
        return None


def _label(record: dict) -> str:
    fields = record.get("fields", {})
    for name in ("legal_name", "name", "label", "full_name", "number", "broker_trip_number", "credential_type"):
        if fields.get(name):
            return str(fields[name])
    when = parse_datetime(fields.get("scheduled_pickup_datetime") or "")
    return f"pickup {timezone.localtime(when):%b %d, %Y %I:%M %p}" if when else ""

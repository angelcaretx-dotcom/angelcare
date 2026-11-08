"""
Base class for every admin screen in the project (ADR 0016 addendum).

Unfold only styles forms, inputs, date pickers and related-object
widgets for ModelAdmins built on `unfold.admin.ModelAdmin`; a plain
`django.contrib.admin.ModelAdmin` renders inside Unfold's frame with
raw browser controls. Every project admin subclasses this instead.

Deletes go to the trash (ADR 0046): the record and everything its delete
takes are kept whole until an Administrator restores or purges them.
"""

from unfold.admin import ModelAdmin


class AngelCareModelAdmin(ModelAdmin):
    # Labels above inputs, tighter spacing: reads like a form, not a table.
    compressed_fields = True
    # Ask before leaving a form with unsaved changes.
    warn_unsaved_form = True

    # --- The trash (ADR 0046) -------------------------------------------

    def _trashable(self) -> bool:
        from trash.services import trashable

        return trashable(self.model)

    def get_deleted_objects(self, objs, request):
        """What the confirmation page lists: the trash's plan, with billed records as the blockers."""
        if not self._trashable():
            return super().get_deleted_objects(objs, request)
        from trash.services import TAKEN_ALONG, plan

        the_plan = plan(list(objs))
        listed = [f"{str(o._meta.verbose_name).capitalize()}: {o}" for o in the_plan.objects]
        perms_needed = {
            str(o._meta.verbose_name) for o in the_plan.objects
            if o._meta.label_lower not in TAKEN_ALONG
            and not request.user.has_perm(f"{o._meta.app_label}.delete_{o._meta.model_name}")
        }
        protected = [f"{str(o._meta.verbose_name).capitalize()}: {o} (billed: set the record Inactive instead)"
                     for o in the_plan.blocked]
        return listed, the_plan.counts(), perms_needed, protected

    def delete_model(self, request, obj):
        if not self._trashable():
            return super().delete_model(request, obj)
        from trash.services import send

        send(obj, user=request.user)

    def delete_queryset(self, request, queryset):
        if not self._trashable():
            return super().delete_queryset(request, queryset)
        from trash.services import send

        for obj in list(queryset):
            if type(obj)._base_manager.filter(pk=obj.pk).exists():  # an earlier one may have taken it along
                send(obj, user=request.user)

    def save_related(self, request, form, formsets, change):
        """Rows deleted in an inline table go to the trash too."""
        from trash.services import send, trashable

        for formset in formsets:
            for inline_form in getattr(formset, "deleted_forms", []):
                obj = inline_form.instance
                if obj.pk and trashable(type(obj)) and type(obj)._base_manager.filter(pk=obj.pk).exists():
                    send(obj, user=request.user)
        super().save_related(request, form, formsets, change)

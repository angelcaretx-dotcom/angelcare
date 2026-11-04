"""
Base class for every admin screen in the project (ADR 0016 addendum).

Unfold only styles forms, inputs, date pickers and related-object
widgets for ModelAdmins built on `unfold.admin.ModelAdmin`; a plain
`django.contrib.admin.ModelAdmin` renders inside Unfold's frame with
raw browser controls. Every project admin subclasses this instead.
"""

from unfold.admin import ModelAdmin


class AngelCareModelAdmin(ModelAdmin):
    # Labels above inputs, tighter spacing: reads like a form, not a table.
    compressed_fields = True
    # Ask before leaving a form with unsaved changes.
    warn_unsaved_form = True

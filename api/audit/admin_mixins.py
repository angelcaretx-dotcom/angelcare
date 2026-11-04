from .services import record_change


class AccessLoggedAdminMixin:
    """
    Records who LOOKED at protected health information, not just who
    changed it (ADR 0027). HIPAA expects an access trail: "which staff
    member opened this rider's record, and when" has to be answerable.

    Logs a successful GET of a record's page as `viewed`, and of the
    list page as `viewed_list` (with the search/filter used, since a
    search for a name is itself a look at that person). Saves are
    already audited by each admin's save_model, so only GETs count
    here. Mix in before admin.ModelAdmin.
    """

    def change_view(self, request, object_id, form_url="", extra_context=None):
        response = super().change_view(request, object_id, form_url, extra_context)
        if request.method == "GET" and response.status_code == 200:
            record_change(
                actor=request.user,
                action="viewed",
                resource_type=self.model._meta.object_name,
                resource_id=object_id,
                source="admin",
            )
        return response

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        if request.method == "GET" and response.status_code == 200:
            query = request.GET.urlencode()
            record_change(
                actor=request.user,
                action="viewed_list",
                resource_type=self.model._meta.object_name,
                resource_id="*",
                after={"query": query[:500]} if query else None,
                source="admin",
            )
        return response

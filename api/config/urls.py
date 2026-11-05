from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView

from trips.views import standing_orders_cron


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    # The bare address (e.g. admin.angelcaretransit.com) goes straight to
    # the admin, so nobody has to type ".../admin/". Not permanent (302):
    # the root stays free for something else later.
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
    # The driver app (ADR 0029): phone-sized, for drivers, not staff.
    path("driver/", include("driver_app.urls")),
    path("healthz/", health_check, name="health-check"),
    path("api/v1/", include("transportation.urls")),
    path("api/v1/rider/", include("riders.urls")),
    path("api/v1/verify/", include("verification.urls")),
    path("api/v1/contact/", include("contact.urls")),
    # Vercel Cron, daily (ADR 0035).
    path("api/v1/cron/standing-orders/", standing_orders_cron, name="cron-standing-orders"),
]

if settings.DEBUG:
    # In production, uploaded files must be served by a real object
    # storage backend, not Django itself -- see documents/models.py.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

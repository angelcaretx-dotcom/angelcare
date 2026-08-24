from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health_check, name="health-check"),
    path("api/v1/", include("transportation.urls")),
]

if settings.DEBUG:
    # In production, uploaded files must be served by a real object
    # storage backend, not Django itself -- see documents/models.py.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

import hmac

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from . import standing


@require_GET
def standing_orders_cron(request):
    """
    The daily job (ADR 0035), called by Vercel Cron with
    "Authorization: Bearer <CRON_SECRET>". Tops up every standing order's
    next 14 days and emails prior-authorization warnings. Safe to repeat.
    """
    secret = settings.CRON_SECRET
    if not secret:
        return JsonResponse({"detail": "Not configured."}, status=503)
    if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {secret}"):
        return JsonResponse({"detail": "Forbidden."}, status=403)
    return JsonResponse(standing.run_daily())

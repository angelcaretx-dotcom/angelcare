"""
The daily job, called by Vercel Cron with
"Authorization: Bearer <CRON_SECRET>" (ADR 0035, 0036). Safe to repeat:
each task only does what's still due.
"""

import hmac
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from compliance.expiry import send_reminders
from trips import standing

logger = logging.getLogger(__name__)


@require_GET
def daily(request):
    secret = settings.CRON_SECRET
    if not secret:
        return JsonResponse({"detail": "Not configured."}, status=503)
    if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {secret}"):
        return JsonResponse({"detail": "Forbidden."}, status=403)
    result = standing.run_daily()
    try:
        result["expiry_reminders"] = send_reminders(standing.texas_today())
    except Exception:  # noqa: BLE001 - the trips above are already made
        logger.exception("Expiry reminders failed.")
        result["expiry_reminders"] = "failed"
    return JsonResponse(result)

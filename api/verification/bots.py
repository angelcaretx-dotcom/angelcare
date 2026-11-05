"""
Bot checks in front of the email code (ADR 0033). Each one is cheap
and stops a different kind of bot:

- a hidden "trap" field people never see, which form-filling bots fill
  in (the request then gets a fake success and nothing is sent);
- a minimum time between the form opening and the code request;
- Cloudflare Turnstile, when its keys are configured.

Per-IP request limits (DRF throttles) and per-email code limits
(services.py) sit on top.
"""

import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TRAP_FIELD = "company_website"
MIN_FORM_SECONDS = 3
NO_TURNSTILE_PER_HOUR = 5
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def tripped_trap(data) -> bool:
    return bool(str(data.get(TRAP_FIELD) or "").strip())


def too_fast(data) -> bool:
    """Filled in faster than a person could. Missing timing counts as too fast."""
    try:
        return float(data.get("elapsed_ms")) < MIN_FORM_SECONDS * 1000
    except (TypeError, ValueError):
        return True


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")


def within_no_turnstile_limit(ip: str) -> bool:
    """
    A visitor whose browser couldn't load Turnstile at all (blocked by an
    ad-blocker, a work network, an old browser) still gets a few codes an
    hour, instead of being turned away from booking. Bots that skip
    Turnstile get no more than that, and still can't read the code.
    """
    key = f"verify-no-turnstile:{ip}"
    cache.add(key, 0, timeout=60 * 60)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, timeout=60 * 60)
        count = 1
    if count > NO_TURNSTILE_PER_HOUR:
        return False
    logger.warning("Code requested without Turnstile (%s of %s this hour).", count, NO_TURNSTILE_PER_HOUR)
    return True


def passes_turnstile(token: str, ip: str) -> bool:
    """
    True when Cloudflare says the visitor is a person. Without a secret
    key configured (local development, tests) the check is skipped. If
    Cloudflare can't be reached, the visitor is let through, because the
    emailed code still has to be entered: an outage there mustn't stop
    people booking rides. No token at all: see within_no_turnstile_limit.
    """
    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        return True
    if not token:
        return within_no_turnstile_limit(ip)
    try:
        response = requests.post(
            TURNSTILE_VERIFY_URL,
            data={"secret": secret, "response": token, "remoteip": ip},
            timeout=5,
        )
        return bool(response.json().get("success"))
    except (requests.RequestException, ValueError):
        logger.warning("Turnstile check unavailable; relying on the email code alone.")
        return True

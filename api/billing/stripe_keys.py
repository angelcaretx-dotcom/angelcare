"""
Which Stripe key is in use, and whether online payment may run with it
(ADR 0050). Pure functions with no Django import: `config/settings.py`
reads them while the settings load, and `billing/payments.py` uses the
same rules, so the two can never disagree.

A key's value never leaves this module's arguments: nothing here logs,
stores or returns it.
"""

LIVE = "live"
TEST = "test"

# Secret keys (sk_) and restricted keys (rk_). A publishable key (pk_) is not a key this
# system can use, so it counts as "no key".
_LIVE_PREFIXES = ("sk_live_", "rk_live_")
_TEST_PREFIXES = ("sk_test_", "rk_test_")


def key_mode(key: str) -> str:
    """ "live", "test", or "" for anything that isn't a Stripe secret or restricted key."""
    key = key or ""
    if key.startswith(_LIVE_PREFIXES):
        return LIVE
    if key.startswith(_TEST_PREFIXES):
        return TEST
    return ""


def webhook_secrets(value: str) -> list[str]:
    """The webhook signing secrets: one, or two separated by a comma while a secret is being rolled."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def configured(key: str, webhook_secret: str, *, debug: bool, allow_test: bool) -> bool:
    """
    Online payment is set up only when BOTH values are present and the key
    may be used here. A test-mode key is refused on a real deployment
    (debug off) unless testing was switched on deliberately; an unreadable
    key is refused everywhere. Anything missing means "off": fail closed.
    """
    if not webhook_secrets(webhook_secret):
        return False
    mode = key_mode(key)
    if mode == LIVE:
        return True
    if mode == TEST:
        return bool(debug or allow_test)
    return False

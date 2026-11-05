"""
Storing encrypted backup archives in a private Supabase Storage bucket,
with retention (ADR 0026). Used by the nightly GitHub Actions job
(.github/workflows/backup.yml) through `manage.py store_backup`.

Talks to Supabase's Storage REST API directly, like
documents.storage.SupabaseStorage (ADR 0012), with the same project URL
and service key. Backups go in their own bucket, never the documents
one, and that bucket is created private.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings

DEFAULT_BUCKET = "backups"
NAME_PATTERN = re.compile(r"^angelcare-backup-(\d{8}T\d{6}Z)\.zip\.enc$")
TIMEOUT = 120


class OffsiteError(Exception):
    pass


@dataclass
class StoreResult:
    stored: str
    deleted: list[str]
    kept: int


def _base() -> str:
    url = settings.SUPABASE_STORAGE_URL.rstrip("/")
    if not (url and settings.SUPABASE_STORAGE_KEY):
        raise OffsiteError("SUPABASE_STORAGE_URL and SUPABASE_STORAGE_KEY must be set.")
    return f"{url}/storage/v1"


def _headers(content_type: str | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_STORAGE_KEY}",
        "apikey": settings.SUPABASE_STORAGE_KEY,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def backup_time(name: str) -> datetime | None:
    """The UTC timestamp in an archive name, or None if it isn't one of ours."""
    match = NAME_PATTERN.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _reason(response) -> str:
    """
    Supabase Storage's short error name ("Bucket not found", "Duplicate",
    "Unauthorized"...): it answers many different problems with HTTP 400,
    so the status alone doesn't say which. Never includes request data.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = str(body.get("error") or body.get("statusCode") or "")[:40] if isinstance(body, dict) else ""
    return f"HTTP {response.status_code}" + (f" ({detail})" if detail else "")


def _get_bucket(bucket: str):
    return requests.get(f"{_base()}/bucket/{bucket}", headers=_headers(), timeout=TIMEOUT)


def ensure_private_bucket(bucket: str) -> None:
    response = _get_bucket(bucket)
    if response.status_code != 200:
        created = requests.post(
            f"{_base()}/bucket",
            headers=_headers("application/json"),
            json={"id": bucket, "name": bucket, "public": False},
            timeout=TIMEOUT,
        )
        if created.status_code not in (200, 201):
            # "Already exists" also comes back as an error: look again.
            response = _get_bucket(bucket)
            if response.status_code != 200:
                raise OffsiteError(
                    f"Couldn't create bucket {bucket!r}: {_reason(created)}; "
                    f"reading it: {_reason(response)}"
                )
        else:
            return
    if response.json().get("public"):
        # Never upload into something world-readable.
        raise OffsiteError(f"Bucket {bucket!r} is public; refusing to store backups in it.")


def list_backups(bucket: str) -> list[str]:
    response = requests.post(
        f"{_base()}/object/list/{bucket}",
        headers=_headers("application/json"),
        json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise OffsiteError(f"Couldn't list bucket {bucket!r}: HTTP {response.status_code}")
    return [item["name"] for item in response.json() if backup_time(item.get("name", ""))]


def store(path, *, bucket: str = DEFAULT_BUCKET, keep_days: int = 30, now: datetime | None = None) -> StoreResult:
    """
    Upload one encrypted archive, then delete archives older than
    keep_days. The newest archive is never deleted, whatever its age, so
    a job that has been failing for a while can't prune away the last
    good backup.
    """
    from pathlib import Path

    path = Path(path)
    if backup_time(path.name) is None:
        raise OffsiteError(f"{path.name!r} isn't an encrypted backup archive name.")
    ensure_private_bucket(bucket)

    with path.open("rb") as fh:
        response = requests.post(
            f"{_base()}/object/{bucket}/{path.name}",
            headers={**_headers("application/octet-stream"), "x-upsert": "false"},
            data=fh,
            timeout=TIMEOUT,
        )
    if response.status_code not in (200, 201):
        raise OffsiteError(f"Upload failed: HTTP {response.status_code}")

    names = list_backups(bucket)
    if path.name not in names:
        raise OffsiteError("Upload reported success but the archive isn't listed in the bucket.")

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    newest = max(names, key=backup_time)
    expired = [n for n in names if backup_time(n) < cutoff and n != newest]
    if expired:
        response = requests.delete(
            f"{_base()}/object/{bucket}",
            headers=_headers("application/json"),
            json={"prefixes": expired},
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            raise OffsiteError(f"Pruning old backups failed: HTTP {response.status_code}")
    return StoreResult(stored=path.name, deleted=expired, kept=len(names) - len(expired))

"""
Django Storage backend for Supabase Storage, using Supabase's own
Storage REST API directly (not the S3-compatible endpoint -- that
needs separate S3 access keys that weren't readily available in the
dashboard; this API works with the same project secret key already
used elsewhere). See docs/decisions/0012-supabase-storage.md.

Only used in production (see STORAGES in config/settings.py) -- local
dev keeps plain FileSystemStorage per ADR 0004, no cloud dependency.
"""

from urllib.parse import quote

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage


class SupabaseStorage(Storage):
    def __init__(self):
        self.base_url = settings.SUPABASE_STORAGE_URL.rstrip("/")
        self.bucket = settings.SUPABASE_STORAGE_BUCKET
        self.key = settings.SUPABASE_STORAGE_KEY

    def _headers(self, content_type: str | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _object_url(self, name: str) -> str:
        # Path segments only, not the whole path -- keeps literal "/"
        # as directory separators while escaping everything else.
        quoted = "/".join(quote(part) for part in name.split("/"))
        return f"{self.base_url}/storage/v1/object/{self.bucket}/{quoted}"

    def _save(self, name: str, content) -> str:
        content.seek(0)
        response = requests.post(
            self._object_url(name),
            headers=self._headers(
                getattr(content.file, "content_type", None)
                or "application/octet-stream"
            ),
            data=content.read(),
            timeout=30,
        )
        response.raise_for_status()
        return name

    def _open(self, name: str, mode: str = "rb"):
        response = requests.get(self._object_url(name), headers=self._headers(), timeout=30)
        response.raise_for_status()
        return ContentFile(response.content, name=name)

    def exists(self, name: str) -> bool:
        response = requests.head(self._object_url(name), headers=self._headers(), timeout=30)
        return response.status_code == 200

    def delete(self, name: str) -> None:
        requests.delete(self._object_url(name), headers=self._headers(), timeout=30)

    def size(self, name: str):
        response = requests.head(self._object_url(name), headers=self._headers(), timeout=30)
        response.raise_for_status()
        return int(response.headers.get("Content-Length", 0))

    def url(self, name: str) -> str:
        # The bucket is private, so a plain object URL 401s for a
        # browser following a link (e.g. from Django admin, which
        # can't attach our Authorization header). A signed URL embeds
        # a short-lived token in the query string instead.
        quoted = "/".join(quote(part) for part in name.split("/"))
        response = requests.post(
            f"{self.base_url}/storage/v1/object/sign/{self.bucket}/{quoted}",
            headers=self._headers("application/json"),
            json={"expiresIn": 3600},
            timeout=30,
        )
        response.raise_for_status()
        signed_path = response.json()["signedURL"]
        return f"{self.base_url}/storage/v1{signed_path}"

    def get_available_name(self, name: str, max_length=None) -> str:
        # Documents are never overwritten in place (see documents/models.py
        # docstring on versioning) -- but Supabase's object API *would*
        # silently overwrite on a name collision, so still dedupe here
        # the same way FileSystemStorage does.
        if not self.exists(name):
            return name
        return super().get_available_name(name, max_length)

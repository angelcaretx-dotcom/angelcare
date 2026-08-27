"""
Separate test module from tests.py so these can mock `requests` freely
without interfering with the FileSystemStorage-based tests there
(which exercise the real model/admin behavior against local storage,
matching what local dev actually uses -- ADR 0004).

HTTP calls are mocked here (no real network/credentials needed to run
these) -- the storage backend was separately verified against the
real Supabase project by hand (curl: upload, download, sign, delete
all confirmed working) before this was wired into Django. See
docs/decisions/0012-supabase-storage.md.
"""

from unittest.mock import MagicMock, patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from .storage import SupabaseStorage


@override_settings(
    SUPABASE_STORAGE_URL="https://project.supabase.co",
    SUPABASE_STORAGE_KEY="sb_secret_test",
    SUPABASE_STORAGE_BUCKET="Documents",
)
class SupabaseStorageTests(TestCase):
    def setUp(self):
        self.storage = SupabaseStorage()

    @patch("documents.storage.requests.post")
    def test_save_posts_content_to_correct_url(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        name = self.storage._save("drivers/license.pdf", ContentFile(b"pdf bytes"))

        self.assertEqual(name, "drivers/license.pdf")
        called_url = mock_post.call_args.args[0]
        self.assertEqual(
            called_url,
            "https://project.supabase.co/storage/v1/object/Documents/drivers/license.pdf",
        )

    @patch("documents.storage.requests.post")
    def test_save_sends_auth_headers(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        self.storage._save("x.pdf", ContentFile(b"data"))

        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sb_secret_test")
        self.assertEqual(headers["apikey"], "sb_secret_test")

    @patch("documents.storage.requests.get")
    def test_open_fetches_and_returns_content(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, content=b"file bytes", raise_for_status=lambda: None
        )

        result = self.storage._open("some/file.pdf")

        self.assertEqual(result.read(), b"file bytes")

    @patch("documents.storage.requests.head")
    def test_exists_true_on_200(self, mock_head):
        mock_head.return_value = MagicMock(status_code=200)
        self.assertTrue(self.storage.exists("some/file.pdf"))

    @patch("documents.storage.requests.head")
    def test_exists_false_on_404(self, mock_head):
        mock_head.return_value = MagicMock(status_code=404)
        self.assertFalse(self.storage.exists("some/file.pdf"))

    @patch("documents.storage.requests.delete")
    def test_delete_calls_correct_url(self, mock_delete):
        mock_delete.return_value = MagicMock(status_code=200)

        self.storage.delete("some/file.pdf")

        called_url = mock_delete.call_args.args[0]
        self.assertEqual(
            called_url, "https://project.supabase.co/storage/v1/object/Documents/some/file.pdf"
        )

    @patch("documents.storage.requests.post")
    def test_url_requests_a_signed_url(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"signedURL": "/object/sign/Documents/x.pdf?token=abc123"},
        )

        url = self.storage.url("x.pdf")

        self.assertEqual(
            url,
            "https://project.supabase.co/storage/v1/object/sign/Documents/x.pdf?token=abc123",
        )
        sign_call_url = mock_post.call_args.args[0]
        self.assertIn("/storage/v1/object/sign/Documents/x.pdf", sign_call_url)

    @patch("documents.storage.requests.head")
    def test_get_available_name_returns_same_name_when_not_taken(self, mock_head):
        mock_head.return_value = MagicMock(status_code=404)
        self.assertEqual(self.storage.get_available_name("free-name.pdf"), "free-name.pdf")

    def test_object_url_escapes_path_segments_but_keeps_slashes(self):
        url = self.storage._object_url("drivers/a file with spaces.pdf")
        self.assertEqual(
            url,
            "https://project.supabase.co/storage/v1/object/Documents/drivers/a%20file%20with%20spaces.pdf",
        )

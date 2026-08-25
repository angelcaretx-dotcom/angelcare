"""
Entry point for Vercel's Python runtime. Vercel's serverless model has
no persistent process -- this wraps the same Django WSGI application
used by `manage.py runserver`/gunicorn locally, so application code is
identical between environments; only how the process is invoked
differs. See docs/decisions/0011-vercel-hosted-api.md for the
tradeoffs of this deployment target versus a persistent host.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

app = get_wsgi_application()

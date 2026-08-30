"""
Django settings for the AngelCare Transit backend.

All environment-specific values (secrets, hosts, database, CORS origins)
come from environment variables — see `.env.example`. Nothing
environment-specific is hard-coded here so this file works unchanged in
dev, staging, and production.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


# --- Core ---

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = env_bool("DJANGO_DEBUG", False)

if not SECRET_KEY:
    if DEBUG:
        # Fixed, clearly-labeled dev-only key so local setup doesn't
        # require generating one. Never used when DEBUG is off.
        SECRET_KEY = "django-insecure-dev-only-key-do-not-use-in-production"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set via environment variable when "
            "DJANGO_DEBUG is not enabled."
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])


# --- Applications ---

INSTALLED_APPS = [
    # "unfold" must precede the admin app entry below -- it overrides
    # admin/*.html templates, and Django's app_directories template
    # loader checks apps in INSTALLED_APPS order (ADR 0016).
    #
    # Explicitly BasicAppConfig, not the bare "unfold" string: bare
    # "unfold" resolves to unfold.apps.DefaultAppConfig (it sets
    # `default = True`), whose ready() unconditionally overwrites
    # `admin.site` with a plain UnfoldAdminSite() instance -- clobbering
    # AngelCareAdminConfig.default_site below regardless of app order.
    # BasicAppConfig is Unfold's own documented escape hatch for
    # exactly this: combining Unfold with another package (django-otp)
    # that also needs to control the admin.site instance.
    "unfold.apps.BasicAppConfig",
    # Replaces the bare "django.contrib.admin" string: registers
    # AngelCareAdminSite (Unfold's UI + django-otp's MFA, ADR 0014 +
    # 0016) as the real admin.site, via Django's documented
    # AdminConfig.default_site mechanism.
    "accounts.apps.AngelCareAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "audit",
    "documents",
    "organization",
    "payers",
    "passengers",
    "drivers",
    "vehicles",
    "transportation",
    "trips",
    "notifications",
    "accounts.apps.AccountsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # templates/ at the project root: currently just
        # templates/admin/login.html, a project-level override of
        # Unfold's own login page (ADR 0016) that adds the MFA
        # otp_token field. DIRS is checked before each app's own
        # templates/ dir, so this wins over Unfold's version.
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database ---
# DATABASE_URL, e.g. postgres://user:pass@host:5432/dbname
# Falls back to local SQLite only when DEBUG is on and DATABASE_URL is unset,
# so local development doesn't require a running PostgreSQL instance.

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # conn_max_age=0: no persistent connections. Correct for a
    # serverless host (each invocation is short-lived; reusing a
    # connection across a frozen/thawed function can hand back a dead
    # one) and harmless for a persistent process, since production
    # sits behind Supabase's PgBouncer pooler anyway (ADR 0011) --
    # Django doesn't need to do its own connection reuse on top of
    # that. DISABLE_SERVER_SIDE_CURSORS is required for PgBouncer's
    # transaction-mode pooling, which doesn't support them.
    DATABASES = {
        "default": {
            **dj_database_url.parse(DATABASE_URL, conn_max_age=0),
            "DISABLE_SERVER_SIDE_CURSORS": True,
        },
    }
elif DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    raise RuntimeError("DATABASE_URL must be set when DJANGO_DEBUG is not enabled.")


# --- Password validation ---

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Internationalization ---
# UTC internally everywhere; convert to local time only at presentation.

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static files ---

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- User-uploaded files (documents/) ---
# Local filesystem storage for local dev (ADR 0004: no cloud account
# needed to develop). Production (Vercel has no persistent disk --
# ADR 0011) uses Supabase Storage via documents.storage.SupabaseStorage
# -- see docs/decisions/0012-supabase-storage.md.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

SUPABASE_STORAGE_URL = os.environ.get("SUPABASE_STORAGE_URL", "")
SUPABASE_STORAGE_KEY = os.environ.get("SUPABASE_STORAGE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "Documents")

if SUPABASE_STORAGE_URL and SUPABASE_STORAGE_KEY:
    DEFAULT_FILE_STORAGE_BACKEND = "documents.storage.SupabaseStorage"
else:
    DEFAULT_FILE_STORAGE_BACKEND = "django.core.files.storage.FileSystemStorage"

STORAGES = {
    "default": {
        "BACKEND": DEFAULT_FILE_STORAGE_BACKEND,
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Only /admin/ has a login page in this project (no separate staff
# frontend). Without this, a login reached without an explicit ?next=
# falls back to Django's own default, /accounts/profile/, which this
# project doesn't define -- a 404 immediately after a successful login.
LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/admin/"


# --- Admin theme (Unfold) ---
# Branded, sidebar-navigation admin UI (ADR 0016) -- combined with MFA
# (ADR 0014) via accounts/admin_site.py::AngelCareAdminSite, registered
# as the real admin.site through accounts/apps.py::AngelCareAdminConfig.
# Color values are plain hex; Unfold converts them internally. "primary"
# is anchored at the two real brand colors also used on the public site
# (web/src/app/globals.css: --color-brand-blue at 400, --color-brand-
# blue-dark at 600), with a hand-built ramp around them for the rest --
# not derived from a design tool, but visually consistent and using the
# exact brand hex at its two known-good points.

from django.templatetags.static import static  # noqa: E402
from django.urls import reverse_lazy  # noqa: E402


def _is_superuser(request):
    return request.user.is_active and request.user.is_superuser


UNFOLD = {
    "SITE_TITLE": "AngelCare Transit Admin",
    "SITE_HEADER": "AngelCare Transit",
    "SITE_SUBHEADER": "Non-Emergency Medical Transportation",
    "SITE_SYMBOL": "airport_shuttle",
    "SITE_LOGO": lambda request: static("accounts/logo.svg"),
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "COLORS": {
        "primary": {
            "50": "#f0f8fc",
            "100": "#dbeef7",
            "200": "#b8ddef",
            "300": "#8ecfe8",
            "400": "#4fb1e4",  # --color-brand-blue
            "500": "#2d90c2",
            "600": "#2d6f91",  # --color-brand-blue-dark
            "700": "#235a75",
            "800": "#1c485e",
            "900": "#163a4b",
            "950": "#0e2530",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "command_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Operations",
                "separator": True,
                "items": [
                    {
                        "title": "Trip Requests",
                        "icon": "inbox",
                        "link": reverse_lazy("admin:transportation_triprequest_changelist"),
                    },
                    {
                        "title": "Trips",
                        "icon": "route",
                        "link": reverse_lazy("admin:trips_trip_changelist"),
                    },
                ],
            },
            {
                "title": "People & Fleet",
                "separator": True,
                "items": [
                    {
                        "title": "Passengers",
                        "icon": "groups",
                        "link": reverse_lazy("admin:passengers_passenger_changelist"),
                    },
                    {
                        "title": "Drivers",
                        "icon": "badge",
                        "link": reverse_lazy("admin:drivers_driver_changelist"),
                    },
                    {
                        "title": "Vehicles",
                        "icon": "directions_car",
                        "link": reverse_lazy("admin:vehicles_vehicle_changelist"),
                    },
                    {
                        "title": "Payers",
                        "icon": "payments",
                        "link": reverse_lazy("admin:payers_payer_changelist"),
                    },
                ],
            },
            {
                "title": "Compliance",
                "separator": True,
                "items": [
                    {
                        "title": "Documents",
                        "icon": "description",
                        "link": reverse_lazy("admin:documents_document_changelist"),
                    },
                ],
            },
            {
                "title": "Organization",
                "separator": True,
                "items": [
                    {
                        "title": "Organization",
                        "icon": "business",
                        "link": reverse_lazy("admin:organization_organization_changelist"),
                    },
                ],
            },
            {
                "title": "System",
                "separator": True,
                "items": [
                    {
                        "title": "Notification Log",
                        "icon": "mail",
                        "link": reverse_lazy("admin:notifications_notificationlog_changelist"),
                    },
                    {
                        "title": "Audit Log",
                        "icon": "history",
                        "link": reverse_lazy("admin:audit_auditlog_changelist"),
                    },
                    {
                        "title": "Staff Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": _is_superuser,
                    },
                    {
                        "title": "Groups & Roles",
                        "icon": "shield_person",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": _is_superuser,
                    },
                ],
            },
        ],
    },
}


# --- CORS ---
# Only the configured frontend origin(s) may call this API from a browser.

CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ALLOWED_ORIGINS", [])


# --- Django REST Framework ---

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/hour",
        "user": "1000/hour",
    },
}


# --- Email / Notifications ---
# Local dev (default): console backend -- emails print to the runserver
# terminal, no credentials or account needed (see ADR 0004). Tests use
# Django's in-memory backend automatically. Production must set
# DJANGO_EMAIL_BACKEND to a real backend (e.g. smtp) and the
# corresponding host/port/user/password -- see .env.example.

DEFAULT_EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", DEFAULT_EMAIL_BACKEND)
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)

DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "angelcaretx@gmail.com")
# Where new-trip-request notifications are sent for staff to review.
STAFF_NOTIFICATION_EMAIL = os.environ.get("STAFF_NOTIFICATION_EMAIL", "angelcaretx@gmail.com")

# Absolute base URLs used to build real, clickable links inside HTML
# emails (e.g. "review this request" -> a specific /admin/ page).
# Templates never hard-code a domain -- see ADR 0015. Defaults are the
# actual current production values so this works out of the box; either
# can be overridden via env var without a code change if the domain
# ever moves (e.g. a future custom admin domain).
SITE_URL = os.environ.get(
    "DJANGO_SITE_URL",
    "http://localhost:8000" if DEBUG else "https://angelcare-api.vercel.app",
).rstrip("/")
WEBSITE_URL = os.environ.get(
    "DJANGO_WEBSITE_URL",
    "http://localhost:3000" if DEBUG else "https://www.angelcaretransit.com",
).rstrip("/")

if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and not DEBUG:
    if not (EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD):
        raise RuntimeError(
            "DJANGO_EMAIL_HOST, DJANGO_EMAIL_HOST_USER, and "
            "DJANGO_EMAIL_HOST_PASSWORD must be set to send real email "
            "in production (DJANGO_DEBUG is not enabled)."
        )


# --- Security (effective only when DEBUG is off) ---

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    X_FRAME_OPTIONS = "DENY"

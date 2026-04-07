import logging
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

VERSION = "0.1.0"

BASE_DIR = Path(__file__).resolve().parent.parent


def env_str(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("true", "1", "yes")


def env_int(name: str, default: int = 0) -> int:
    return int(os.getenv(name, str(default)))


# --- Security ---

SECRET_KEY = env_str("DJANGO_SECRET_KEY", "change-me-in-production")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env_str("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",")]

if not DEBUG and SECRET_KEY == "change-me-in-production":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a unique, unpredictable value when DEBUG is False.")

# --- Application ---

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/auth/login/"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "import_export",
    "simple_history",
    "django_celery_results",
    "django_celery_beat",
    "corsheaders",
    "constance",
    "constance.backends.database",
    # Project apps
    "core",
    "accounts",
    "organization",
    "projects",
    "pages",
    "fossil",
    "testdata",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "core.middleware.current_user.CurrentUserMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.sidebar",
            ],
        },
    },
]

# --- Database ---

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("POSTGRES_DB", "fossilrepo"),
        "USER": env_str("POSTGRES_USER", "dbadmin"),
        "PASSWORD": env_str("POSTGRES_PASSWORD", "Password123"),
        "HOST": env_str("POSTGRES_HOST", "localhost"),
        "PORT": env_str("POSTGRES_PORT", "5432"),
    }
}

# --- Cache ---

REDIS_URL = env_str("REDIS_URL", "redis://localhost:6379/1")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# --- Auth ---

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30  # 30 days
CSRF_COOKIE_HTTPONLY = True

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# --- i18n ---

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static ---

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "assets"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Media / S3 ---

USE_S3 = env_bool("USE_S3", False)

if USE_S3:
    STORAGES["default"] = {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"}
    AWS_ACCESS_KEY_ID = env_str("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env_str("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env_str("AWS_STORAGE_BUCKET_NAME", "fossilrepo")
    AWS_S3_ENDPOINT_URL = env_str("AWS_S3_ENDPOINT_URL", "")
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH = True
else:
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"

# --- Email ---

EMAIL_BACKEND = env_str("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 1025)
DEFAULT_FROM_EMAIL = env_str("FROM_EMAIL", "no-reply@fossilrepo.local")

# --- Celery ---

CELERY_BROKER_URL = env_str("CELERY_BROKER", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = "django-db"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 3600
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_SCHEDULE = {
    "fossil-sync-metadata": {
        "task": "fossil.sync_metadata",
        "schedule": 300.0,  # every 5 minutes
    },
    "fossil-check-upstream": {
        "task": "fossil.check_upstream",
        "schedule": 900.0,  # every 15 minutes
    },
    "fossil-dispatch-notifications": {
        "task": "fossil.dispatch_notifications",
        "schedule": 300.0,  # every 5 minutes
    },
    "fossil-daily-digest": {
        "task": "fossil.send_digest",
        "schedule": 86400.0,  # daily
        "kwargs": {"mode": "daily"},
    },
    "fossil-weekly-digest": {
        "task": "fossil.send_digest",
        "schedule": 604800.0,  # weekly
        "kwargs": {"mode": "weekly"},
    },
}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- CORS ---

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [o.strip() for o in env_str("CORS_ALLOWED_ORIGINS", "http://localhost:8000").split(",")]

CSRF_TRUSTED_ORIGINS = [o.strip() for o in env_str("CSRF_TRUSTED_ORIGINS", "http://localhost:8000").split(",")]

# --- Rate limiting ---

RATELIMIT_VIEW = "django.views.defaults.permission_denied"

# --- Constance (runtime feature toggles) ---

CONSTANCE_BACKEND = "constance.backends.database.DatabaseBackend"
CONSTANCE_CONFIG = {
    "SITE_NAME": ("Fossilrepo", "Display name for the site"),
    "FOSSIL_DATA_DIR": ("/data/repos", "Directory where .fossil repository files are stored"),
    "FOSSIL_STORE_IN_DB": (False, "Store binary snapshots of .fossil files via Django file storage"),
    "FOSSIL_S3_TRACKING": (False, "Track S3/Litestream replication keys and versions"),
    "FOSSIL_S3_BUCKET": ("", "S3 bucket name for Fossil repo replication"),
    "FOSSIL_BINARY_PATH": ("fossil", "Path to the fossil binary"),
    # Git sync settings
    "GIT_SYNC_MODE": ("disabled", "Default sync mode: disabled, on_change, scheduled, both"),
    "GIT_SYNC_SCHEDULE": ("*/15 * * * *", "Default cron schedule for Git sync"),
    "GIT_MIRROR_DIR": ("/data/git-mirrors", "Directory for Git mirror checkouts"),
    "GIT_SSH_KEY_DIR": ("/data/ssh-keys", "Directory for SSH key storage"),
    "GITHUB_OAUTH_CLIENT_ID": ("", "GitHub OAuth App Client ID"),
    "GITHUB_OAUTH_CLIENT_SECRET": ("", "GitHub OAuth App Client Secret"),
    "GITLAB_OAUTH_CLIENT_ID": ("", "GitLab OAuth App Client ID"),
    "GITLAB_OAUTH_CLIENT_SECRET": ("", "GitLab OAuth App Client Secret"),
    # Cloudflare Turnstile (optional bot protection on login)
    "TURNSTILE_ENABLED": (False, "Enable Cloudflare Turnstile on the login page"),
    "TURNSTILE_SITE_KEY": ("", "Cloudflare Turnstile site key (public)"),
    "TURNSTILE_SECRET_KEY": ("", "Cloudflare Turnstile secret key (server-side verification)"),
}
CONSTANCE_CONFIG_FIELDSETS = {
    "General": ("SITE_NAME",),
    "Fossil Storage": ("FOSSIL_DATA_DIR", "FOSSIL_STORE_IN_DB", "FOSSIL_S3_TRACKING", "FOSSIL_S3_BUCKET", "FOSSIL_BINARY_PATH"),
    "Git Sync": ("GIT_SYNC_MODE", "GIT_SYNC_SCHEDULE", "GIT_MIRROR_DIR", "GIT_SSH_KEY_DIR"),
    "GitHub OAuth": ("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET"),
    "GitLab OAuth": ("GITLAB_OAUTH_CLIENT_ID", "GITLAB_OAUTH_CLIENT_SECRET"),
    "Cloudflare Turnstile": ("TURNSTILE_ENABLED", "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"),
}

# --- Sentry ---

SENTRY_DSN = env_str("SENTRY_DSN")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)

# --- Logging ---

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# --- Import/Export ---

IMPORT_FORMATS = []
EXPORT_FORMATS = []

# --- Admin ---

ADMIN_SITE_HEADER = "Fossilrepo"
ADMIN_SITE_TITLE = f"Fossilrepo Admin {VERSION}"

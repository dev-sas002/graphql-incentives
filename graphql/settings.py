"""
Django settings.

Every knob is environment-driven with a working default, so the app boots with
no configuration at all — that is what makes the Docker image useful on first
run and what keeps the test suite hermetic.

Note the absence of a database: this project is a GraphQL *client* with no
models, no migrations and no user accounts. ``DATABASES`` is deliberately empty
rather than pointing at an unused SQLite file.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- core -----------------------------------------------------------------

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")

# Defaults to off: this project is deployed publicly, and a DEBUG default of
# True would leak tracebacks and settings on any unhandled error.
DEBUG = _env_bool("DJANGO_DEBUG", False)

_allowed_hosts = os.getenv(
    "DJANGO_ALLOWED_HOSTS",
    "127.0.0.1,localhost,.vercel.app,.now.sh",
)
ALLOWED_HOSTS = [host.strip() for host in _allowed_hosts.split(",") if host.strip()]

# --- incentives data source ----------------------------------------------

# "graphql" hits the real upstream API; "sample" generates deterministic data
# so the app is never empty and never needs the network. docker-compose sets
# "sample" so a first boot is populated without credentials or connectivity.
INCENTIVES_SOURCE = os.getenv("INCENTIVES_SOURCE", "graphql").strip().lower()

GRAPHQL_API_URL = os.getenv("GRAPHQL_API_URL", "https://api.taomarketcap.com/graphql")
GRAPHQL_SUBNET_UID = _env_int("GRAPHQL_SUBNET_UID", 18)
GRAPHQL_REQUEST_TIMEOUT = _env_float("GRAPHQL_REQUEST_TIMEOUT", 10.0)
GRAPHQL_MAX_RETRIES = _env_int("GRAPHQL_MAX_RETRIES", 2)
GRAPHQL_RETRY_BACKOFF = _env_float("GRAPHQL_RETRY_BACKOFF", 0.5)

# --- chart ----------------------------------------------------------------

INCENTIVES_RENDERER = os.getenv("INCENTIVES_RENDERER", "matplotlib_png")
# A subnet holds up to 256 UIDs; the chart's categorical palette has eight
# fixed slots, so eight is the hard ceiling. Six is the default because six
# lines are comfortably readable where eight start to crowd each other.
INCENTIVES_TOP_N = _env_int("INCENTIVES_TOP_N", 6)

# --- caching --------------------------------------------------------------

# Seconds a rendered chart stays cached. Incentive data moves on the order of
# minutes, and rendering is the expensive part of a request.
INCENTIVES_CACHE_TTL = _env_int("INCENTIVES_CACHE_TTL", 300)

_redis_url = os.getenv("REDIS_URL", "").strip()
if _redis_url:
    # Shared across gunicorn workers and across replicas.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
            "KEY_PREFIX": "incentives",
        }
    }
else:
    # Per-process fallback: correct, just not shared between workers.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "incentives-locmem",
        }
    }

# --- optional AI summary --------------------------------------------------

# When no key is present the app falls back to the deterministic summary
# computed in ``incentives.analysis.describe``; nothing else changes.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
AI_SUMMARY_ENABLED = _env_bool("AI_SUMMARY_ENABLED", True)
AI_SUMMARY_TIMEOUT = _env_float("AI_SUMMARY_TIMEOUT", 20.0)

# --- application ----------------------------------------------------------

INSTALLED_APPS = [
    # Only what a template-rendering client actually needs: no auth, no
    # sessions, no admin, no contenttypes -- none of them have a database to
    # talk to. ``humanize`` supplies ``intcomma`` for the stat tiles.
    "django.contrib.humanize",
    "django.contrib.staticfiles",
    "incentives",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "graphql.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "graphql.wsgi.application"

# No models, no migrations, no database connection.
DATABASES = {}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    # Compressed, but not hashed: the app serves a single stylesheet, so the
    # manifest backend would buy nothing and would make every code path that
    # has not run ``collectstatic`` (tests, ``runserver``) fail on a missing
    # ``staticfiles.json``.
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)-7s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}

# Security hardening that only applies once the app is served over HTTPS.
# Most PaaS front ends terminate TLS and forward the original scheme in
# X-Forwarded-Proto. Both branches assign every name, so reloading this module
# with a different environment cannot leave a stale production value behind.
if DEBUG:
    SECURE_PROXY_SSL_HEADER = None
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
else:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

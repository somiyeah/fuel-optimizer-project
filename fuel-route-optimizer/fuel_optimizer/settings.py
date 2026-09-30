"""
Django settings for the fuel_optimizer project.

Every value that differs between local development and production is read from
environment variables, with defaults that work out of the box for local runs.
See README.md ("Production notes") for the variables to set when deploying.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean flag such as DJANGO_DEBUG=false from the environment."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str]) -> list[str]:
    """Read a comma separated list such as DJANGO_ALLOWED_HOSTS=a.com,b.com."""
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #

# The default key only exists so the project runs locally without setup.
# Always set DJANGO_SECRET_KEY in any shared or production environment.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-development-key-change-me-before-deploying",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "[::1]"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "api.apps.ApiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fuel_optimizer.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fuel_optimizer.wsgi.application"
ASGI_APPLICATION = "fuel_optimizer.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3")),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# In-process cache for OSRM routes and online geocoding results. Swap for Redis
# or Memcached when running several worker processes.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-optimizer",
        "TIMEOUT": 3600,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

# --------------------------------------------------------------------------- #
# Django REST Framework
# --------------------------------------------------------------------------- #

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        *(["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
    ],
    # The API is public and stateless, so no session auth (and no CSRF on POST).
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
}

# --------------------------------------------------------------------------- #
# Fuel optimizer
# --------------------------------------------------------------------------- #

FUEL_OPTIMIZER = {
    # Vehicle model. A full tank covers 500 miles; planning keeps a 20 mile
    # safety margin, so no leg between fuel purchases exceeds 480 miles.
    "MAX_RANGE_MILES": env_float("FUEL_MAX_RANGE_MILES", 500.0),
    "SAFETY_BUFFER_MILES": env_float("FUEL_SAFETY_BUFFER_MILES", 20.0),
    "MILES_PER_GALLON": env_float("FUEL_MILES_PER_GALLON", 10.0),
    # Prices within this many USD/gal are treated as equal by the optimizer,
    # which avoids 1-2 gallon "top-up" stops for sub-cent savings.
    # 0 gives the exact minimum cost. Overridable per request.
    "PRICE_TOLERANCE_PER_GALLON": env_float("FUEL_PRICE_TOLERANCE_PER_GALLON", 0.05),
    # Stops buying fewer gallons than this are folded into the previous or next
    # stop when the tank allows. 0 disables folding. Overridable per request.
    "MIN_PURCHASE_GALLONS": env_float("FUEL_MIN_PURCHASE_GALLONS", 5.0),
    # The trip starts with an empty tank: the first purchase happens at the
    # cheapest station within this many route miles of the start.
    "ORIGIN_FILL_WINDOW_MILES": 50.0,
    # If no station lies on the first stretch of the route, price the first
    # fill at the nearest station to the start within this straight-line radius.
    "ORIGIN_FALLBACK_RADIUS_MILES": 50.0,
    # Station to route matching. Station coordinates are city centroids, so the
    # corridor is wider when the station's highway (parsed from its address)
    # is one the route actually drives on near that point.
    "CORRIDOR_MILES_HIGHWAY_MATCH": 15.0,
    "CORRIDOR_MILES_DEFAULT": 5.0,
    "CORRIDOR_MILES_HIGHWAY_MISMATCH": 2.0,
    "HIGHWAY_MATCH_WINDOW_MILES": 30.0,
    "ROUTE_DENSIFY_MILES": 0.5,
    # Response and caching.
    "MAX_OUTPUT_GEOMETRY_POINTS": 2500,
    "ROUTE_CACHE_SECONDS": 6 * 3600,
    "GEOCODE_CACHE_SECONDS": 24 * 3600,
    # OSRM routing (exactly one request per uncached route).
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    "OSRM_PROFILE": os.environ.get("OSRM_PROFILE", "driving"),
    "OSRM_USE_STEPS": env_bool("OSRM_USE_STEPS", True),
    "OSRM_CONNECT_TIMEOUT_SECONDS": env_float("OSRM_CONNECT_TIMEOUT_SECONDS", 5.0),
    "OSRM_READ_TIMEOUT_SECONDS": env_float("OSRM_READ_TIMEOUT_SECONDS", 25.0),
    # Geocoding. City/State, ZIP and coordinates resolve offline from the bundled
    # gazetteer. Only free-form street addresses fall back to Nominatim.
    "GEOCODER_ONLINE_FALLBACK": env_bool("GEOCODER_ONLINE_FALLBACK", True),
    "NOMINATIM_URL": os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"),
    "NOMINATIM_EMAIL": os.environ.get("NOMINATIM_EMAIL", ""),
    "NOMINATIM_TIMEOUT_SECONDS": env_float("NOMINATIM_TIMEOUT_SECONDS", 8.0),
    "HTTP_USER_AGENT": os.environ.get("HTTP_USER_AGENT", "fuel-route-optimizer/1.0 (Django take-home assessment)"),
    # A coordinate counts as "in the USA" when a US ZIP centroid lies within
    # this distance of it.
    "US_POINT_MAX_DISTANCE_MILES": 30.0,
    # Background map tiles for the /route/map/ page. OpenStreetMap's servers
    # require the browser to send a Referer header; the map view sets a
    # referrer policy that allows it (see RouteMapView).
    "MAP_TILE_URL": os.environ.get("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
    "MAP_TILE_ATTRIBUTION": os.environ.get(
        "MAP_TILE_ATTRIBUTION",
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    ),
    # Data files.
    "FUEL_PRICES_CSV": BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
    "GAZETTEER_PLACES_FILE": BASE_DIR / "data" / "gazetteer" / "us_places.csv.gz",
    "GAZETTEER_ZIPS_FILE": BASE_DIR / "data" / "gazetteer" / "us_zips.csv.gz",
    # Build lookup tables in the background when the web server starts, so the
    # first request is as fast as the rest.
    "WARM_CACHES_ON_STARTUP": env_bool("FUEL_WARM_CACHES_ON_STARTUP", True),
}

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "loggers": {
        "api": {"handlers": ["console"], "level": os.environ.get("API_LOG_LEVEL", "INFO")},
    },
}

# Keep `manage.py test` output readable; tests assert on behaviour, not logs.
if len(sys.argv) > 1 and sys.argv[1] == "test":
    LOGGING["loggers"]["api"]["level"] = "CRITICAL"

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = "DENY"
    # Turn these on once the site is served only over HTTPS.
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

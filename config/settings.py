import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Tiny .env reader so local runs need no `$env:` lines. Real environment variables always win."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


DEBUG = env("DJANGO_DEBUG", "0") == "1"

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DEBUG is off.")
if not DEBUG and (len(SECRET_KEY) < 40 or SECRET_KEY.upper().startswith(("CHANGE", "PASTE"))):
    raise RuntimeError("DJANGO_SECRET_KEY is a placeholder or too short. Generate one: "
                       'python3 -c "import secrets;print(secrets.token_urlsafe(50))"')

_default_hosts = "localhost,127.0.0.1" if DEBUG else ""
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
# Render (demo hosting) tells the app its own address; allow it automatically.
_render_host = env("RENDER_EXTERNAL_HOSTNAME")
if _render_host:
    if _render_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_render_host)
    if f"https://{_render_host}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "store",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "store.security.LoginRateLimitMiddleware",
    "store.security.PermissionsPolicyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "store.context.site",
    ]},
}]

# --- Database (PostgreSQL; SQLite only for quick local experiments) ---------
if env("USE_SQLITE") == "1":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
elif env("DATABASE_URL"):
    # Hosted demo (Render gives one DATABASE_URL instead of separate POSTGRES_* values).
    from urllib.parse import unquote, urlparse
    _u = urlparse(env("DATABASE_URL"))
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _u.path.lstrip("/"),
        "USER": unquote(_u.username or ""),
        "PASSWORD": unquote(_u.password or ""),
        "HOST": _u.hostname or "",
        "PORT": str(_u.port or 5432),
        "CONN_MAX_AGE": 60,
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "clbr"),
        "USER": env("POSTGRES_USER", "clbr"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache (used for rate limiting). Use Redis in production so all
#     gunicorn workers share counters. ----------------------------------------
if env("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "ops:login"
LOGIN_REDIRECT_URL = "ops:home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
# Demo hosting only: let Django itself serve /static/ and /media/ (the full server uses Caddy for this).
# Leave off (default) on the real DigitalOcean server.
SERVE_FILES = env("SERVE_FILES", "0") == "1"

# --- Security ---------------------------------------------------------------
ADMIN_URL = env("DJANGO_ADMIN_URL", "manage-clbr/").strip("/") + "/"     # always exactly one trailing slash
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024          # JSON bodies are tiny; reject anything big
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 8 * 60 * 60                  # staff sessions last one work shift
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
# Nominatim asks apps to identify themselves; sending only the origin keeps paths private.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

BEHIND_PROXY = env("BEHIND_PROXY", "0") == "1"
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start with 1 hour so a mistake can be undone. After the live site has worked for a few days,
    # set HSTS_SECONDS=31536000 in .env.
    SECURE_HSTS_SECONDS = int(env("HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    if BEHIND_PROXY:
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Store settings -----------------------------------------------------------
ORDER_HOLD_HOURS = int(env("ORDER_HOLD_HOURS", "24"))
INSTAGRAM_URL = "https://www.instagram.com/clbrwear_co/"
SIZE_OPTIONS = ["M", "L", "XL", "XXL", "ONE SIZE"]      # sizes offered on the staff "add shirt" form
MAX_QTY_PER_LINE = 5
MAX_CART_LINES = 10
ORDER_RATE_LIMIT = (20, 600)      # per IP per 10 min. Generous on purpose: Philippine mobile networks put MANY customers behind one IP.
                                  # Real abuse is capped separately by MAX_PENDING_PER_PHONE.
MAX_PENDING_PER_PHONE = 3         # unverified orders one phone number may hold (anti stock-hoarding)
LOGIN_RATE_LIMIT = (10, 900)      # login POSTs per IP per 15 minutes (staff login AND Django admin login)

# --- Storefront content (edit these, no code changes needed) ---------------------
# Featured artists. Add a "bio" line to any of them to show a short blurb on their card.
ARTISTS = [
    {"name": "DJ CLARK", "role": "DJ", "image": "store/img/clark-1.jpg", "bio": ""},
    {"name": "DJ ROX",   "role": "DJ", "image": "store/img/rox-1.jpg",   "bio": ""},
    {"name": "MC MYL",   "role": "MC", "image": "store/img/myl-4.jpg",   "bio": ""},
]
ARTISTS_INSTAGRAM = "https://www.instagram.com/clbrwear_co/"

# Size chart (inches, garment laid flat). Boxy crop fit, sizes M to XXL only.
SIZE_CHART_UNIT = "in"
SIZE_CHART_CONFIRMED = True
SIZE_CHART = [
    # size, width, length, sleeve
    ("M",   22, 24.5, 9),
    ("L",   23, 25.5, 10),
    ("XL",  24, 26.5, 11),
    ("XXL", 25, 27.5, 11.5),
]

# --- Logging: everything to stderr (docker compose logs web). Order/verify/cancel events use logger "store". ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"store": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

import sys                                      # keep `manage.py test` output readable
if "test" in sys.argv:
    LOGGING["loggers"]["store"]["level"] = "CRITICAL"
    LOGGING["loggers"]["django.request"] = {"handlers": [], "level": "CRITICAL", "propagate": False}

# --- Business details shown in the footer, Contact, Privacy and Terms pages ----------------------
BUSINESS = {
    "name": "CLBR WEAR CO.",
    "tagline": "The crosshair doesn\u2019t miss",
    "city": "Roxas City, Capiz, Philippines",
    "email": "clbrwear@gmail.com",
    "phone": "0977 269 5421",
    "instagram": INSTAGRAM_URL,
    "instagram_handle": "@clbrwear_co",
    "facebook_name": "CLBR WEAR CO.",
    "dti_registration": "",          # e.g. "DTI Business Name Reg. No. 1234567" — shown in the footer when filled in
}

# --- Store policies (shown on /shipping-returns/ and /terms/). !!! SAMPLE TERMS: replace with YOUR real
#     policy, then set POLICIES_CONFIRMED = True. While False, those pages show a visible DRAFT banner. !!!
POLICIES_CONFIRMED = False
POLICIES = {
    "delivery_time": "about 3 to 7 working days within the Visayas and 5 to 10 working days elsewhere in the Philippines, depending on the courier",
    "shipping_fee": "paid by you directly to the courier when the parcel is delivered, on top of the item price. The amount depends on your location and the courier's rate",
    "return_window_days": 3,
    "courier": "J&T Express or LBC",
}
SITE_URL = env("SITE_URL", "")        # e.g. https://clbrwear.shop  (used in sitemap/OG tags; falls back to the request host)


# CLBR Radio (SoundCloud). By default the SoundCloud player is moved off-screen and only the CLBR player shows.
# Set RADIO_HIDE_PLAYER=0 in .env to show SoundCloud's own player instead. Check SoundCloud's current terms before using it in production.
RADIO_HIDE_PLAYER = env("RADIO_HIDE_PLAYER", "1") == "1"

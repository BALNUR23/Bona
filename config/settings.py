import os
import ipaddress
from importlib.util import find_spec
from pathlib import Path

try:
    import dj_database_url
except ModuleNotFoundError:  # pragma: no cover
    dj_database_url = None
from corsheaders.defaults import default_headers
from django.templatetags.static import static
from django.urls import reverse_lazy

# ======================
# BASE
# ======================
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    return _env_bool(name, default)

# ======================
# SECURITY
# ======================
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    if DEBUG:
        _secret_key = "dev-secret-key-change-me"
    else:
        raise RuntimeError("SECRET_KEY environment variable must be set in production (DEBUG=false)")
SECRET_KEY = _secret_key

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "your.domain.com,server_ip").split(",")
    if host.strip()
]

# ======================
# APPLICATIONS
# ======================
HAS_UNFOLD = find_spec("unfold") is not None
HAS_WHITENOISE = find_spec("whitenoise") is not None

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "django_ckeditor_5",

    # Local apps
    "accounts.apps.AccountsConfig",
    "regulations.apps.RegulationsConfig",
    "reports.apps.ReportsConfig",
    "content.apps.ContentConfig",
    "common.apps.CommonConfig",
    "work_schedule.apps.WorkScheduleConfig",
    "security.apps.SecurityConfig",
    "onboarding_core.apps.OnboardingCoreConfig",
    "apps.audit.apps.AuditConfig",
    "apps.attendance.apps.AttendanceConfig",
    "apps.tasks.apps.TasksConfig",
    "apps.payroll.apps.PayrollConfig",
    "apps.kb.apps.KbConfig",
    "apps.metrics.apps.MetricsConfig",
    "apps.bpm.apps.BpmConfig",
]

if HAS_UNFOLD:
    INSTALLED_APPS = [
        "unfold",
        "unfold.contrib.filters",
        *INSTALLED_APPS,
    ]

# ======================
# MIDDLEWARE
# ======================
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.middleware.APILanguageMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "config.middleware.APIErrorEnvelopeMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if HAS_WHITENOISE:
    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

# ======================
# URLS / WSGI
# ======================
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# ======================
# DATABASE
# ======================
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_SSL_REQUIRE = _env_bool("DB_SSL_REQUIRE", False)
if DATABASE_URL and dj_database_url is not None:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=DB_SSL_REQUIRE,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "onboarding"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }

# ======================
# AUTH
# ======================
AUTH_USER_MODEL = "accounts.User"

# ======================
# TEMPLATES
# ======================
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
            ],
        },
    },
]

# ======================
# STATIC / MEDIA
# ======================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
if HAS_WHITENOISE:
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ======================
# I18N / L10N
# ======================
LANGUAGE_CODE = "ru"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
    ("kg", "Kyrgyz"),
]

# ======================
# Admin UI (Unfold)
# ======================
if HAS_UNFOLD:
    def _is_admin_like(request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True
        role = getattr(user, "role", None)
        role_name = getattr(role, "name", "")
        return role_name in {"SUPER_ADMIN", "ADMINISTRATOR", "ADMIN"}

    def _is_employee(request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        role = getattr(user, "role", None)
        role_name = getattr(role, "name", "")
        return role_name in {"EMPLOYEE", "TEAMLEAD"}

    UNFOLD = {
        "SITE_TITLE": "HRM Админ-панель",
        "SITE_HEADER": "HRM Администрирование",
        "SITE_SUBHEADER": "Корпоративная система",
        "SITE_BRAND": "HRM",
        "SITE_ICON": None,
        "SITE_SYMBOL": None,
        "SHOW_HISTORY": True,
        "SHOW_VIEW_ON_SITE": False,
        "THEME": "light",
        "SIDEBAR": {
            "show_search": True,
            "show_all_applications": False,
            "navigation": [
                {
                    "title": "Мои разделы",
                    "separator": True,
                    "items": [
                        {
                            "title": "Главная",
                            "icon": "home",
                            "link": reverse_lazy("admin:index"),
                            "permission": lambda request: _is_admin_like(request) or _is_employee(request),
                        },
                        {
                            "title": "Регламенты",
                            "icon": "description",
                            "link": "/admin/regulations/regulation/",
                            "permission": lambda request: _is_admin_like(request) or _is_employee(request),
                        },
                        {
                            "title": "График работы",
                            "icon": "calendar_month",
                            "link": "/admin/work_schedule/weeklyworkplan/",
                            "permission": _is_employee,
                        },
                        {
                            "title": "Отметка",
                            "icon": "fact_check",
                            "link": "/admin/attendance/check-in/",
                            "permission": _is_employee,
                        },
                        {
                            "title": "Профиль",
                            "icon": "person",
                            "link": "/admin/accounts/user/",
                            "permission": lambda request: _is_admin_like(request) or _is_employee(request),
                        },
                        {
                            "title": "Инструкция",
                            "icon": "menu_book",
                            "link": "/admin/content/instruction/",
                            "permission": _is_employee,
                        },
                    ],
                },
                {
                    "title": "Управление",
                    "separator": True,
                    "items": [
                        {
                            "title": "Обзор",
                            "icon": "dashboard",
                            "link": reverse_lazy("admin:index"),
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Пользователи",
                            "icon": "group",
                            "link": "/admin/accounts/user/",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Контент",
                            "icon": "article",
                            "link": "/admin/content/",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Онбординг / Отчёты",
                            "icon": "school",
                            "link": "/admin/onboarding/",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Графики работы",
                            "icon": "calendar_month",
                            "link": "/admin/work_schedule",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Недельные планы",
                            "icon": "event_note",
                            "link": "/admin/work_schedule/weeklyworkplan/",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Посещаемость",
                            "icon": "fact_check",
                            "link": "/admin/attendance/attendancemark/",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Check-in сессии",
                            "icon": "pin_drop",
                            "link": "/admin/attendance/attendancesession/",
                            "permission": _is_admin_like,
                        },
                        {
                            "title": "Обратная связь",
                            "icon": "feedback",
                            "link": "/admin/content/feedback/",
                            "permission": _is_admin_like,
                        },
                    ],
                },
            ],
        },
        "STYLES": [
            lambda request: static("admin/css/global-admin.css"),
        ],
    }

# ======================
# CKEDITOR 5
# ======================
CKEDITOR_5_CONFIGS = {
    "default": {
        "toolbar": [
            "heading",
            "|",
            "bold",
            "italic",
            "link",
            "bulletedList",
            "numberedList",
            "blockQuote",
            "insertImage",
            "undo",
            "redo",
        ],
    }
}
CKEDITOR_5_UPLOAD_PATH = "uploads/ckeditor5/"

# ======================
# DRF
# ======================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "user": "1000/day",
        "anon": "20/minute",
        "login": "5/minute",
        "password_reset_request": "5/hour",
        "password_reset_confirm": "10/hour",
    },
}

# Local development can generate many parallel/polling API calls from SPA tabs.
# Relax throttle limits in DEBUG to avoid false-positive 429 responses.
if DEBUG:
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["user"] = os.environ.get("DRF_THROTTLE_USER", "100000/day")
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"] = os.environ.get("DRF_THROTTLE_ANON", "10000/day")

SIMPLE_JWT = {
    "TOKEN_OBTAIN_SERIALIZER": "accounts.tokens.CustomTokenSerializer",
}

# Unified audit facade settings.
# No DB changes required; both backends use existing models.
AUDIT_PRIMARY_BACKEND = os.environ.get("AUDIT_PRIMARY_BACKEND", "accounts")
AUDIT_LEGACY_BACKEND = os.environ.get("AUDIT_LEGACY_BACKEND", "security")
AUDIT_WRITE_MODE = os.environ.get("AUDIT_WRITE_MODE", "primary_only")

# Office geofence for one-time attendance check-in.
OFFICE_GEOFENCE_LATITUDE = (
    float(os.environ["OFFICE_GEOFENCE_LATITUDE"])
    if os.environ.get("OFFICE_GEOFENCE_LATITUDE")
    else None
)
OFFICE_GEOFENCE_LONGITUDE = (
    float(os.environ["OFFICE_GEOFENCE_LONGITUDE"])
    if os.environ.get("OFFICE_GEOFENCE_LONGITUDE")
    else None
)
OFFICE_GEOFENCE_RADIUS_M = int(os.environ.get("OFFICE_GEOFENCE_RADIUS_M", "150"))
OFFICE_IP_NETWORKS = [
    ipaddress.ip_network(value.strip())
    for value in os.environ.get(
        "OFFICE_IP_NETWORKS",
        "127.0.0.1/32,192.168.1.0/24,192.168.10.0/24,10.0.0.0/16",
    ).split(",")
    if value.strip()
]

SPECTACULAR_SETTINGS = {
    "TITLE": "Onboarding API",
    "DESCRIPTION": "API for onboarding platform",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SORT_OPERATIONS": True,
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
    },
}

# ======================
# CORS / CSRF
# ======================
CORS_ALLOW_HEADERS = list(default_headers) + [
    "authorization",
]

CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "https://your.domain.com").split(",")
    if origin.strip()
]

CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


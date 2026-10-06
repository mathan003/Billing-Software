"""
Django settings for billing_backend project.
Configured for:
- Local MySQL development & Cloud Deployments (Render, Clever Cloud, Aiven, etc.)
- REST API for Windows App auto-sync
- Mobile & Web responsive interface
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-nar1$lbea4&0a*chzw+7mme*yhlbu+@#p-2zi4o-^+_+je7o9p")

DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

# Allowed hosts for local and cloud deployment (e.g. Render)
ALLOWED_HOSTS = ["*"]

# CSRF Trusted Origins for Render, Railway, and local desktop app
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "https://*.onrender.com",
    "https://*.render.com",
    "https://*.railway.app",
    "https://*.up.railway.app",
]
railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
if railway_domain:
    CSRF_TRUSTED_ORIGINS.extend([
        f"https://{railway_domain}",
        f"http://{railway_domain}",
    ])
custom_csrf = os.getenv("CSRF_TRUSTED_ORIGINS")
if custom_csrf:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in custom_csrf.split(",")])

# Honor 'X-Forwarded-Proto' header for request.is_secure() behind Railway/Render reverse proxies
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    # Third-party apps
    "rest_framework",
    # Local apps
    "billing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Serve static files efficiently in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "billing.middleware.SessionSecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# Long-lived persistent session: 1 year (User stays logged in until manual logout)
SESSION_COOKIE_AGE = 31536000  # 365 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True

ROOT_URLCONF = "billing_backend.urls"

# Django REST Framework configuration for Postman & API access
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

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
                "billing.context_processors.session_security_context",
            ],
        },
    },
]

WSGI_APPLICATION = "billing_backend.wsgi.application"

# Database Configuration
# Priority:
# 1. Cloud Database URL (DATABASE_URL, MYSQL_URL, POSTGRES_URL)
# 2. Remote MySQL host (DB_HOST, MYSQLHOST)
# 3. Cloud deployment fallback to SQLite if no remote database configured (prevents Railway crash)
# 4. Local MySQL with auto-fallback to SQLite if MySQL daemon is not running

IS_RAILWAY = bool(
    os.getenv("RAILWAY_ENVIRONMENT_NAME")
    or os.getenv("RAILWAY_ENVIRONMENT")
    or os.getenv("RAILWAY_PROJECT_ID")
    or os.getenv("RAILWAY_SERVICE_ID")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")
)
IS_RENDER = bool(os.getenv("RENDER"))
IS_HEROKU = bool(os.getenv("DYNO"))
IS_CLOUD = IS_RAILWAY or IS_RENDER or IS_HEROKU or bool(os.getenv("K_SERVICE"))

DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("MYSQL_URL")
    or os.getenv("MYSQL_PRIVATE_URL")
    or os.getenv("MYSQL_PUBLIC_URL")
    or os.getenv("POSTGRES_URL")
)

DB_HOST = os.getenv("DB_HOST") or os.getenv("MYSQLHOST")
DB_PORT = os.getenv("DB_PORT") or os.getenv("MYSQLPORT", "3306")
DB_USER = os.getenv("DB_USER") or os.getenv("MYSQLUSER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD") or os.getenv("MYSQLPASSWORD", "root")
DB_NAME = os.getenv("DB_NAME") or os.getenv("MYSQLDATABASE", "billing_db")
USE_SQLITE = os.getenv("USE_SQLITE", "False").lower() in ("true", "1", "yes")

LOCAL_DB_PATH = os.getenv("LOCAL_DB_PATH")
sqlite_db_file = Path(LOCAL_DB_PATH) if LOCAL_DB_PATH else (BASE_DIR / "billing_local.sqlite3")

def _check_port_open(host, port, timeout=0.8):
    import socket
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

if DATABASE_URL:
    db_config = dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
    )
    if "mysql" in db_config.get("ENGINE", ""):
        db_config.setdefault("OPTIONS", {})
        db_config["OPTIONS"].setdefault("charset", "utf8mb4")
        db_config["OPTIONS"].setdefault("init_command", "SET sql_mode='STRICT_TRANS_TABLES'")
    DATABASES = {"default": db_config}

elif DB_HOST and DB_HOST not in ("127.0.0.1", "localhost"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": DB_NAME,
            "USER": DB_USER,
            "PASSWORD": DB_PASSWORD,
            "HOST": DB_HOST,
            "PORT": DB_PORT,
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }

elif USE_SQLITE or IS_CLOUD:
    # On Cloud (Railway / Render) without an attached remote database, or when requested:
    # Use SQLite so migrations and server run cleanly without connection errors.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_db_file,
        }
    }

else:
    # Local development: check if MySQL server is actually listening on localhost
    host = DB_HOST or "127.0.0.1"
    port = DB_PORT or "3306"
    if _check_port_open(host, port):
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "NAME": DB_NAME,
                "USER": DB_USER,
                "PASSWORD": DB_PASSWORD,
                "HOST": host,
                "PORT": port,
                "OPTIONS": {
                    "charset": "utf8mb4",
                    "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
                },
            }
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": sqlite_db_file,
            }
        }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework Settings
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

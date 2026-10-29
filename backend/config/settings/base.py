"""Settings shared by every environment. Secrets come only from environment variables."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required")
    return value


def env_bool(name, default=False):
    return env(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.platform_admin",
    "apps.industry",
    "apps.messaging_templates",
    "apps.jobs",
    "apps.subscribers",
    "apps.schedules",
    "apps.portal",
    "apps.content_view",
    "apps.change_requests",
    "apps.reports",
    "apps.billing",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    # First: health checks arrive with the task's IP as Host, before ALLOWED_HOSTS is checked.
    "apps.core.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.TenantMiddleware",
    "apps.platform_admin.middleware.TenantWriteGuardMiddleware",
    # After TenantMiddleware, so audit entries see request.support_session_id.
    "apps.audit.context.AuditContextMiddleware",
]

# Callable that returns the tenant (or tenant id) for a request, or None.
TENANCY_RESOLVER = "apps.platform_admin.tenancy.resolve_request_tenant"
# Paths where a suspended tenant / support session may still write (sign-in and the admin API).
TENANT_GUARD_EXEMPT_PATHS = ("/api/auth/", "/api/admin/")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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

# Two database roles (see docs/admin-console-spec.md Section 8):
# - "app" (default): app_user, used by the API and workers. Cannot bypass Row-Level Security.
# - "owner": app_owner, owns the tables. Used only for migrations (and tests, which run migrations).
DATABASE_ROLES = {
    "app": (env("POSTGRES_APP_USER", "app_user"), env("POSTGRES_APP_PASSWORD", "")),
    "owner": (env("POSTGRES_OWNER_USER", "app_owner"), env("POSTGRES_OWNER_PASSWORD", "")),
}
DATABASE_ROLE = env("DJANGO_DB_ROLE", "app")
if DATABASE_ROLE not in DATABASE_ROLES:
    raise ImproperlyConfigured("DJANGO_DB_ROLE must be 'app' or 'owner'")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "schedule_saas"),
        "USER": DATABASE_ROLES[DATABASE_ROLE][0],
        "PASSWORD": DATABASE_ROLES[DATABASE_ROLE][1],
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        # "require" on AWS (RDS enforces TLS); "prefer" locally.
        "OPTIONS": {"sslmode": env("POSTGRES_SSLMODE", "prefer")},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_URL", REDIS_URL),
        "KEY_PREFIX": "saas",
    }
}

# Files (sign-off evidence, imports, recordings) on S3. Locally the endpoint is the SeaweedFS S3 API
# in docker-compose; on AWS the endpoint is left empty and credentials come from the task role.
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", "schedule-saas-local")
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL") or None
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", "ap-south-1")
# Endpoint browsers use for signed links when it differs from the API's (local docker-compose only).
AWS_S3_PUBLIC_ENDPOINT_URL = env("AWS_S3_PUBLIC_ENDPOINT_URL") or None
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "endpoint_url": AWS_S3_ENDPOINT_URL,
            "region_name": AWS_S3_REGION_NAME,
            "access_key": env("AWS_ACCESS_KEY_ID") or None,
            "secret_key": env("AWS_SECRET_ACCESS_KEY") or None,
            "addressing_style": "path",
            "signature_version": "s3v4",
            "default_acl": None,
            "file_overwrite": False,
            "querystring_auth": True,
            "querystring_expire": 300,  # signed download links last 5 minutes
        },
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# WhatsApp Business provider (spec 3.4). The stub makes no network calls; Phase 2 adds Meta.
WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", "apps.messaging_templates.providers.StubProvider")
# Development-only "simulate Meta / the client" controls (see messaging_templates/dev.py). They also
# require the stub provider and DEBUG, so they can never be on in staging or production.
WHATSAPP_DEV_CONTROLS = env_bool("WHATSAPP_DEV_CONTROLS", True)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.accounts.permissions.TwoFactorIfRequired",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Schedule Confirmation SaaS API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Separate request/response components: response types mark every returned field as present.
    "COMPONENT_SPLIT_REQUEST": True,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.core.openapi.require_response_fields",
    ],
    # Several models have a "status" field; name each enum so the generated client reads well.
    "ENUM_NAME_OVERRIDES": {
        "TenantStatusEnum": "apps.core.models.Tenant.Status",
        "MembershipStatusEnum": "apps.accounts.models.Membership.Status",
        "SupportSessionEndReasonEnum": "apps.platform_admin.models.SupportSession.EndReason",
        "TenantRoleEnum": "apps.accounts.models.TenantRole",
        "LanguageEnum": "apps.core.constants.Language",
        "TemplateVersionStatusEnum": "apps.messaging_templates.models.VersionStatus",
        "BookingOutcomeEnum": "apps.schedules.models.Outcome",
        "AnswerOutcomeEnum": "apps.industry.serializers.OUTCOMES",
        "PortalRoleEnum": "apps.portal.views.PORTAL_ROLES",
        "BookingStatusEnum": "apps.schedules.models.Schedule.Status",
        "ChangeRequestStatusEnum": "apps.change_requests.models.ChangeRequest.Status",
        "ChangeRequestTypeEnum": "apps.change_requests.models.ChangeRequest.RequestType",
        "AttemptChannelEnum": "apps.schedules.models.Attempt.Channel",
        "BillingChannelEnum": "apps.billing.models.Channel",
        "PaymentProviderEnum": "apps.billing.models.Provider",
        "SubscriberStatusEnum": "apps.subscribers.models.Subscriber.Status",
    },
}

OTP_TOTP_ISSUER = "Schedule Confirmation"

# Email: console locally; SES (SMTP interface) in staging/production via env.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@localhost")
# Amazon SES (EMAIL_BACKEND=apps.core.email.SesEmailBackend) uses the task's IAM role.
AWS_SES_REGION_NAME = env("AWS_SES_REGION_NAME", "ap-south-1")
AWS_SES_CONFIGURATION_SET = env("AWS_SES_CONFIGURATION_SET", "")

# Behind the AWS load balancer, the client IP is the first X-Forwarded-For entry (staging, prod).
AUDIT_TRUST_X_FORWARDED_FOR = env_bool("AUDIT_TRUST_X_FORWARDED_FOR", False)

ADMIN_BASE_URL = env("ADMIN_BASE_URL", "http://localhost:3000")
PORTAL_BASE_URL = env("PORTAL_BASE_URL", "http://localhost:3001")

CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "expire-support-sessions": {
        "task": "apps.platform_admin.tasks.expire_support_sessions",
        "schedule": 60.0,
    },
    "sync-whatsapp-template-statuses": {
        "task": "apps.messaging_templates.tasks.sync_template_statuses",
        "schedule": 600.0,
    },
    "fail-stale-imports": {
        "task": "apps.subscribers.tasks.fail_stale_imports",
        "schedule": 300.0,
    },
    "check-auto-top-ups": {
        "task": "apps.billing.tasks.check_auto_top_ups",
        "schedule": 900.0,
    },
    "apply-billing-dates": {
        "task": "apps.billing.tasks.apply_billing_dates",
        "schedule": 3600.0,
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# --- Billing (portal spec 2.9) --------------------------------------------------------------
# The seller printed on GST invoices. Set these per environment; the defaults are placeholders.
SELLER_LEGAL_NAME = env("SELLER_LEGAL_NAME", "Seller name not set (SELLER_LEGAL_NAME)")
SELLER_GSTIN = env("SELLER_GSTIN", "")
SELLER_ADDRESS = env("SELLER_ADDRESS", "Address not set (SELLER_ADDRESS)")
SELLER_STATE = env("SELLER_STATE", "KL")  # state code; same state as the buyer -> CGST + SGST
SELLER_EMAIL = env("SELLER_EMAIL", "billing@example.com")
INVOICE_PREFIX = env("INVOICE_PREFIX", "SC")
INVOICE_SAC_CODE = env("INVOICE_SAC_CODE", "998319")  # confirm with the accountant
GST_RATE_PERCENT = int(env("GST_RATE_PERCENT", "18"))
INVOICE_FONT_PATH = env("INVOICE_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
LOW_BALANCE_DEFAULT_PAISE = 100_000  # ₹1,000 until there are 14 days of usage

# --- Payments (portal spec 2.9, Step 21) ----------------------------------------------------
# Providers in order of preference; a provider without credentials is skipped.
PAYMENT_PROVIDERS = env_list("PAYMENT_PROVIDERS", "razorpay,cashfree")
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_API_BASE = env("RAZORPAY_API_BASE", "https://api.razorpay.com/v1")
CASHFREE_APP_ID = env("CASHFREE_APP_ID", "")
CASHFREE_SECRET_KEY = env("CASHFREE_SECRET_KEY", "")
CASHFREE_API_BASE = env("CASHFREE_API_BASE", "https://sandbox.cashfree.com/pg")
CASHFREE_API_VERSION = env("CASHFREE_API_VERSION", "2023-08-01")
CASHFREE_MODE = env("CASHFREE_MODE", "sandbox")  # sandbox | production (the browser SDK's mode)
# The local payment simulator; only ever used when DEBUG is on.
PAYMENT_STUB_ENABLED = env_bool("PAYMENT_STUB_ENABLED", True)
TOP_UP_MIN_PAISE = 100_000  # ₹1,000
TOP_UP_MAX_PAISE = 50_000_000  # ₹5,00,000 per payment
AUTO_TOP_UP_MIN_PAISE = 100_000  # ₹1,000
AUTO_TOP_UP_MAX_PAISE = 1_500_000  # ₹15,000
TESTING = False

from .base import *  # noqa: F403
from .base import DATABASE_ROLES, DATABASES, INSTALLED_APPS

# Tests run migrations, so they connect as the table owner. FORCE ROW LEVEL SECURITY still applies
# to it, and tests/test_rls_app_role.py separately connects as the app role.
DATABASES["default"]["USER"], DATABASES["default"]["PASSWORD"] = DATABASE_ROLES["owner"]
INSTALLED_APPS = [*INSTALLED_APPS, "tests.tenancy_app"]

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

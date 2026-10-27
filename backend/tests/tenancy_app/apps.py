from django.apps import AppConfig


class TenancyAppConfig(AppConfig):
    """Test-only app (installed by config.settings.test) with one tenant-scoped model."""

    name = "tests.tenancy_app"
    label = "tenancy_app"

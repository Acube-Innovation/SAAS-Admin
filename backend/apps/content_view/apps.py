from django.apps import AppConfig


class ContentViewConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content_view"
    verbose_name = "Content view (portal, read-only)"

from django.apps import AppConfig


class ChangeRequestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.change_requests"
    verbose_name = "Change requests"

    def ready(self):
        from apps.messaging_templates.signals import version_went_live

        from .services import close_for_live_version

        version_went_live.connect(close_for_live_version, dispatch_uid="change_requests_live")

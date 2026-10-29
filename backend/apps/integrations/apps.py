from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Integrations (API keys, webhooks, public API)"

    def ready(self):
        from . import events

        events.connect()

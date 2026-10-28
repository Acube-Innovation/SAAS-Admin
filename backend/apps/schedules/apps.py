from django.apps import AppConfig


class SchedulesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.schedules"
    verbose_name = "Schedules (bookings)"

    def ready(self):
        from apps.subscribers.importer import register_adapter

        from .importer import BookingAdapter

        register_adapter(BookingAdapter())

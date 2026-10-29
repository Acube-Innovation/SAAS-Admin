from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    verbose_name = "Billing (wallet, prices, invoices, payments)"

    def ready(self):
        from .payment_services import on_low_balance
        from .services import wallet_low_balance

        wallet_low_balance.connect(on_low_balance, dispatch_uid="billing_auto_top_up")

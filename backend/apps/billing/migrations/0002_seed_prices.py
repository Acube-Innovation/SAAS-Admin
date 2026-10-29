"""Default platform prices (admin spec 2.11). Super admins change them from Step A2."""

import datetime as dt

from django.db import migrations

# A copy (migrations must not import app code that may change): models.DEFAULT_PRICES.
PRICES = {"whatsapp_utility": 85, "whatsapp_service": 35, "voice_minute": 150}


def seed(apps, schema_editor):
    PriceList = apps.get_model("billing", "PriceList")
    for channel, paise in PRICES.items():
        PriceList.objects.get_or_create(
            channel=channel, effective_from=dt.date(2026, 4, 1), defaults={"price_paise": paise}
        )


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

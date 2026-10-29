"""
Placeholder plans until the approved list arrives (admin spec 2.11; Step A2 lets super admins edit
them). Marked is_placeholder so screens can say so.
"""

from django.db import migrations

PLANS = [
    ("starter", "Starter (placeholder)", 199_900, 1_000, False, False, False),
    ("growth", "Growth (placeholder)", 499_900, 5_000, False, True, True),
    ("pro", "Pro (placeholder)", 999_900, 20_000, True, True, True),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for code, name, fee, limit, voice, api, staff in PLANS:
        Plan.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "monthly_fee_paise": fee,
                "max_active_subscribers": limit,
                "features": {"voice_ai": voice, "public_api": api, "staff_app": staff},
                "is_placeholder": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_payments_and_plans")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

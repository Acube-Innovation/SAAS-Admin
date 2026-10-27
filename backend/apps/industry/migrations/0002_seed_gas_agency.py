from django.db import migrations

from apps.industry.seed import GAS_AGENCY, seed_template


def seed(apps, schema_editor):
    seed_template(
        apps.get_model("industry", "IndustryTemplate"),
        apps.get_model("industry", "ScheduleType"),
        apps.get_model("industry", "FieldDefinition"),
        apps.get_model("industry", "IndustryTemplateVersion"),
        GAS_AGENCY,
    )


class Migration(migrations.Migration):
    """Seed the gas_agency industry template (spec 2.6)."""

    dependencies = [("industry", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

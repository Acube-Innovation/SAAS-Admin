from django.db import migrations

import apps.core.rls


class Migration(migrations.Migration):
    """Let 2FA-verified platform staff read memberships across tenants (clients list, owner email)."""

    dependencies = [("accounts", "0001_initial")]

    operations = [
        apps.core.rls.AlterRLSPolicy(
            "membership",
            extra_using=f"({apps.core.rls.OWN_ROWS}) OR ({apps.core.rls.PLATFORM_ACCESS})",
            previous={"extra_using": apps.core.rls.OWN_ROWS},
        ),
    ]

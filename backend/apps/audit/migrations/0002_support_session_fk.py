import django.db.models.deletion
from django.db import migrations, models

# The column stays audit_auditlog.support_session_id; only a foreign key constraint is added, so no
# audit row is rewritten (the insert-only trigger would refuse that anyway).
ADD_FK = """
ALTER TABLE audit_auditlog
    ADD CONSTRAINT audit_auditlog_support_session_fk FOREIGN KEY (support_session_id)
    REFERENCES platform_admin_supportsession (id) DEFERRABLE INITIALLY DEFERRED;
"""
DROP_FK = "ALTER TABLE audit_auditlog DROP CONSTRAINT audit_auditlog_support_session_fk;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("platform_admin", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="auditlog", name="support_session_id"),
                migrations.AddField(
                    model_name="auditlog",
                    name="support_session",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="platform_admin.supportsession",
                    ),
                ),
            ],
            database_operations=[migrations.RunSQL(ADD_FK, DROP_FK)],
        ),
    ]

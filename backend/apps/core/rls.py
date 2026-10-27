"""Migration operations for PostgreSQL Row-Level Security on tenant tables."""

from django.db.migrations.operations.base import Operation

POLICY_NAME = "tenant_isolation"
# NULLIF: with no tenant set the setting is '' (or NULL), which matches no rows instead of erroring.
TENANT_EXPRESSION = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


class EnableRLS(Operation):
    """
    Enable and force RLS on a TenantScopedModel's table, with a policy that only allows rows whose
    tenant_id equals app.tenant_id, for reads (USING) and writes (WITH CHECK).

    Usage in a migration:  EnableRLS("subscriber")
    """

    reversible = True
    reduces_to_sql = True

    def __init__(self, model_name):
        self.model_name = model_name

    def state_forwards(self, app_label, state):
        pass

    def _table(self, app_label, state):
        return state.apps.get_model(app_label, self.model_name)._meta.db_table

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        table = schema_editor.quote_name(self._table(app_label, to_state))
        schema_editor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        schema_editor.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING (tenant_id = {TENANT_EXPRESSION}) "
            f"WITH CHECK (tenant_id = {TENANT_EXPRESSION})"
        )

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        table = schema_editor.quote_name(self._table(app_label, from_state))
        schema_editor.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        schema_editor.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    def describe(self):
        return f"Enable row-level security on {self.model_name}"

    @property
    def migration_name_fragment(self):
        return f"rls_{self.model_name.lower()}"

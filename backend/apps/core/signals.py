from django.dispatch import Signal

# Sent when a suspended tenant is activated again. Args: tenant, suspended_at.
# Hook for the Phase 2 reminder engine: reminders held during the suspension whose send time has
# passed are marked Skipped (Workflow 3.2).
tenant_reactivated = Signal()

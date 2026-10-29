"""
Local development only: credit a tenant's wallet as a paid top-up would (invoice + credit), until
the payment providers arrive in Step 21.

    python manage.py wallet_top_up "Pala Demo Gas" 1000      # ₹1,000 of credit (+ GST invoiced)
"""

import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.billing.pdf import rupees
from apps.billing.services import record_top_up
from apps.core.models import Tenant


class Command(BaseCommand):
    help = "Credit a wallet with a top-up and issue its GST invoice (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("tenant", help="Tenant name or id")
        parser.add_argument(
            "amount", type=int, help="Credit in rupees (GST is added on the invoice)"
        )
        parser.add_argument("--allow-non-debug", action="store_true")

    def handle(self, *args, tenant, amount: int, allow_non_debug, **options):
        if not settings.DEBUG and not allow_non_debug:
            raise CommandError("wallet_top_up is for local development (DEBUG=True).")
        if amount < 1:
            raise CommandError("Give a positive amount in rupees.")
        found = Tenant.objects.filter(name=tenant).first()
        if found is None:
            try:
                found = Tenant.objects.filter(pk=uuid.UUID(tenant)).first()
            except ValueError:
                found = None
        if found is None:
            raise CommandError(f"No tenant {tenant!r}.")
        entry = record_top_up(found, amount * 100, payment_reference=f"dev-{uuid.uuid4().hex[:12]}")
        self.stdout.write(
            f"Credited {rupees(entry.amount_paise)}; balance {rupees(entry.balance_after_paise)}; "
            f"invoice {entry.invoice.number if entry.invoice else '-'}"
        )

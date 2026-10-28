"""
Issue a fresh invite for an invited member and print its link (development and end-to-end tests,
where invite emails only reach the worker log). Earlier links for that member stop working.

    python manage.py issue_invite_link owner@agency.test --tenant "Kochi Gas Agency"
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Membership, User
from apps.accounts.services import invite_url, resend_invite
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context


class Command(BaseCommand):
    help = "Print a new invite link for an invited member (local development only)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--tenant", required=True, help="Agency name or id")
        parser.add_argument("--allow-non-debug", action="store_true")

    def handle(self, *args, email, tenant, allow_non_debug, **options):
        if not settings.DEBUG and not allow_non_debug:
            raise CommandError("issue_invite_link is for local development (DEBUG=True).")
        agency = Tenant.objects.filter(name=tenant).first()
        if agency is None:
            try:
                agency = Tenant.objects.filter(pk=tenant).first()
            except Exception:  # not a UUID
                agency = None
        if agency is None:
            raise CommandError(f"No agency {tenant!r}.")
        user = User.objects.filter(email__iexact=email).first()
        with tenant_context(agency):
            membership = (
                Membership.objects.filter(tenant=agency, user=user).first() if user else None
            )
            if membership is None:
                raise CommandError(f"{email} is not a member of {agency.name}.")
            token = resend_invite(membership)
        self.stdout.write(invite_url(token))

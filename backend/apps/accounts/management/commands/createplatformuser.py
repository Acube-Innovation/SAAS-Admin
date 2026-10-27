import getpass
import sys

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import PlatformRole, User
from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit


class Command(BaseCommand):
    help = "Create a platform user (super_admin or support). They set up 2FA at first sign-in."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--full-name", default="")
        parser.add_argument("--role", choices=PlatformRole.values, default=PlatformRole.SUPER_ADMIN)
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the password from stdin instead of prompting (for scripts).",
        )

    def handle(self, *args, email, full_name, role, password_stdin, **options):
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"A user with email {email} already exists.")
        if password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        user = User(email=email.lower(), full_name=full_name, platform_role=role)
        try:
            password_validation.validate_password(password, user)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        user.set_password(password)
        with transaction.atomic():
            user.save()
            write_audit(Action.CREATE, user, after=snapshot(user), tenant=None, actor=None)
        self.stdout.write(self.style.SUCCESS(f"Created {role} {user.email}"))

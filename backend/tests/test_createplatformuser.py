import io

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import PlatformRole, User

pytestmark = pytest.mark.django_db


def run(monkeypatch, password, *args):
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    out = io.StringIO()
    call_command("createplatformuser", *args, "--password-stdin", stdout=out)
    return out.getvalue()


def test_creates_a_super_admin(monkeypatch):
    out = run(monkeypatch, "a-long-admin-passphrase", "--email", "Admin@Example.com")

    user = User.objects.get(email="admin@example.com")
    assert user.platform_role == PlatformRole.SUPER_ADMIN
    assert user.check_password("a-long-admin-passphrase")
    assert "Created super_admin admin@example.com" in out


def test_creates_a_support_user(monkeypatch):
    run(monkeypatch, "a-long-support-passphrase", "--email", "s@example.com", "--role", "support")
    assert User.objects.get(email="s@example.com").platform_role == PlatformRole.SUPPORT


def test_rejects_a_short_password(monkeypatch):
    with pytest.raises(CommandError, match="at least 12 characters"):
        run(monkeypatch, "short", "--email", "admin@example.com")
    assert not User.objects.exists()


def test_rejects_a_duplicate_email(monkeypatch):
    run(monkeypatch, "a-long-admin-passphrase", "--email", "admin@example.com")
    with pytest.raises(CommandError, match="already exists"):
        run(monkeypatch, "a-long-admin-passphrase", "--email", "ADMIN@example.com")

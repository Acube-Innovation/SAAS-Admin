"""Pieces the AWS deployment relies on: health checks by IP, SES email, managed-DB role set-up."""

from unittest import mock

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.core.email import SesEmailBackend
from apps.core.management.commands.ensure_db_roles import statements

pytestmark = pytest.mark.django_db


# --- health checks by private IP -------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=["admin.staging.example.com"])
def test_health_answers_load_balancer_checks_by_ip(client):
    with mock.patch("apps.core.views.check_redis"):
        response = client.get("/api/health", HTTP_HOST="10.20.11.37:8000")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@override_settings(ALLOWED_HOSTS=["admin.staging.example.com"])
def test_other_paths_still_check_the_host(client):
    response = client.get("/api/auth/me", HTTP_HOST="10.20.11.37:8000")
    assert response.status_code == 400


@override_settings(ALLOWED_HOSTS=["admin.staging.example.com"])
def test_health_reports_failures_by_ip_too(client):
    with mock.patch("apps.core.views.check_redis", side_effect=ConnectionError("down")):
        response = client.get("/api/health", HTTP_HOST="10.20.11.37")
    assert response.status_code == 503


# --- SES email backend ----------------------------------------------------------------------


@override_settings(DEFAULT_FROM_EMAIL="no-reply@staging.example.com", AWS_SES_CONFIGURATION_SET="")
def test_ses_backend_sends_raw_mime():
    backend = SesEmailBackend()
    backend.client = mock.Mock()
    message = mail.EmailMultiAlternatives(
        "Your invitation", "Plain body", to=["owner@agency.test"], connection=backend
    )
    message.attach_alternative("<p>HTML body</p>", "text/html")

    assert backend.send_messages([message]) == 1
    request = backend.client.send_email.call_args.kwargs
    assert request["FromEmailAddress"] == "no-reply@staging.example.com"
    assert request["Destination"] == {"ToAddresses": ["owner@agency.test"]}
    raw = request["Content"]["Raw"]["Data"].decode()
    assert "Subject: Your invitation" in raw
    assert "<p>HTML body</p>" in raw
    assert "ConfigurationSetName" not in request


@override_settings(AWS_SES_CONFIGURATION_SET="staging-events")
def test_ses_backend_uses_the_configuration_set_and_skips_empty_messages():
    backend = SesEmailBackend()
    backend.client = mock.Mock()
    messages = [mail.EmailMessage("x", "y", to=[]), mail.EmailMessage("a", "b", to=["a@b.test"])]
    assert backend.send_messages(messages) == 1
    assert backend.client.send_email.call_args.kwargs["ConfigurationSetName"] == "staging-events"


def test_ses_backend_raises_unless_fail_silently():
    backend = SesEmailBackend()
    backend.client = mock.Mock()
    backend.client.send_email.side_effect = RuntimeError("throttled")
    message = mail.EmailMessage("a", "b", to=["a@b.test"])
    with pytest.raises(RuntimeError):
        backend.send_messages([message])
    backend.fail_silently = True
    assert backend.send_messages([message]) == 0


# --- ensure_db_roles ------------------------------------------------------------------------


def rendered(existing):
    from django.db import connection as django_connection

    with django_connection.cursor() as cursor:
        raw = cursor.connection
        return [
            s.as_string(raw)
            for s in statements("schedule_saas", "app_owner", "o-pw", "app_user", "a-pw", existing)
        ]


def test_roles_are_created_without_bypassing_rls():
    sql = rendered(existing=set())
    assert sql[0] == (
        'CREATE ROLE "app_owner" LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOBYPASSRLS '
        "PASSWORD 'o-pw'"
    )
    assert sql[1].startswith('CREATE ROLE "app_user" LOGIN NOSUPERUSER NOCREATEDB')
    assert "NOBYPASSRLS" in sql[1]
    assert sql[2:] == [
        'GRANT "app_owner" TO CURRENT_USER',
        'ALTER DATABASE "schedule_saas" OWNER TO "app_owner"',
        'GRANT CONNECT ON DATABASE "schedule_saas" TO "app_user"',
    ]


def test_existing_roles_only_get_the_current_password():
    # A non-superuser admin (RDS) may not restate SUPERUSER/BYPASSRLS, so ALTER leaves them alone.
    sql = rendered(existing={"app_owner", "app_user"})
    assert sql[0] == "ALTER ROLE \"app_owner\" LOGIN PASSWORD 'o-pw'"
    assert sql[1] == "ALTER ROLE \"app_user\" LOGIN PASSWORD 'a-pw'"


def fake_connection(roles):
    connection = mock.MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchall.return_value = roles
    return connection


@pytest.fixture
def admin_env(monkeypatch, settings):
    monkeypatch.setenv("POSTGRES_ADMIN_USER", "dbadmin")
    monkeypatch.setenv("POSTGRES_ADMIN_PASSWORD", "admin-pw")
    settings.DATABASE_ROLES = {"owner": ("app_owner", "o-pw"), "app": ("app_user", "a-pw")}


def test_ensure_db_roles_refuses_roles_that_bypass_rls(admin_env):
    connection = fake_connection([("app_owner", False, False), ("app_user", False, True)])
    with (
        mock.patch("psycopg.connect", return_value=connection),
        pytest.raises(CommandError, match="app_user can bypass Row-Level Security"),
    ):
        call_command("ensure_db_roles")
    assert connection.execute.call_count == 1  # only the lookup; nothing was changed


def test_ensure_db_roles_runs_every_statement(admin_env):
    connection = fake_connection([])
    with mock.patch("psycopg.connect", return_value=connection) as connect:
        call_command("ensure_db_roles", stdout=mock.Mock())
    assert connect.call_args.kwargs["user"] == "dbadmin"
    assert connect.call_args.kwargs["autocommit"] is True
    assert connection.execute.call_count == 1 + 5  # lookup + 2 roles, grant, owner, connect


def test_ensure_db_roles_needs_the_admin_credentials(monkeypatch):
    monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
    monkeypatch.delenv("POSTGRES_ADMIN_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="POSTGRES_ADMIN_USER"):
        call_command("ensure_db_roles")

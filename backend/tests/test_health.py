from unittest import mock

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_health_returns_200_when_dependencies_are_up(client):
    with mock.patch("apps.core.views.check_redis"):
        response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


def test_health_needs_no_authentication(client):
    with mock.patch("apps.core.views.check_redis"):
        response = client.get("/api/health")

    assert response.status_code == 200


def test_health_returns_503_when_redis_is_down(client):
    with mock.patch("apps.core.views.check_redis", side_effect=ConnectionError("down")):
        response = client.get(reverse("health"))

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "ok", "redis": "error"}


def test_health_returns_503_when_database_is_down(client):
    with (
        mock.patch("apps.core.views.check_redis"),
        mock.patch("apps.core.views.check_database", side_effect=RuntimeError("down")),
    ):
        response = client.get(reverse("health"))

    assert response.status_code == 503
    assert response.json()["database"] == "error"


def test_openapi_schema_is_published(client):
    response = client.get("/api/schema")

    assert response.status_code == 200
    assert b"/api/health" in response.content


def test_schema_marks_response_fields_present_but_keeps_request_fields_optional(client):
    import yaml

    schema = yaml.safe_load(client.get("/api/schema").content)
    components = schema["components"]["schemas"]
    tenant = components["Tenant"]
    assert {"status", "timezone", "daily_send_cap", "industry_settings"} <= set(tenant["required"])
    request = components["TenantCreateRequest"]
    assert "daily_send_cap" not in request.get("required", [])  # has a default: optional input
    assert "PatchedTenantRequest" in components

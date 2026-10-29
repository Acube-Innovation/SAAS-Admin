"""Plans, price list, overrides and plan changes in the admin console (spec 2.11, Workflow 3.8)."""

import datetime as dt

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.billing import plans
from apps.billing import services as billing
from apps.billing.models import Invoice, Plan, PriceList, PriceOverride, Subscription
from apps.billing.payment_services import subscription_for
from apps.core.tenancy import db_context, tenant_context
from apps.schedules.models import Attempt, Schedule
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()
TOMORROW = TODAY + dt.timedelta(days=1)


def staff(user):
    client = APIClient()
    sign_in(client, user)
    return client


@pytest.fixture
def admin(super_admin):
    return staff(super_admin)


@pytest.fixture
def support(support_user):
    return staff(support_user)


def on_day(monkeypatch, day):
    """Make "today" (India) a given day for the code under test."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: day)


# --- plans ------------------------------------------------------------------------------------


def test_super_admin_adds_and_edits_plans(admin, support):
    plans_url = "/api/admin/plans"
    listed = admin.get(plans_url).json()
    assert [p["code"] for p in listed] == ["starter", "growth", "pro"]
    assert all(p["is_placeholder"] for p in listed)
    body = {
        "code": "enterprise",
        "name": "Enterprise",
        "monthly_fee_paise": 2_499_900,
        "max_active_subscribers": 100_000,
        "features": {"voice_ai": True, "public_api": True, "staff_app": True},
        "is_active": True,
    }
    created = admin.post(plans_url, body, format="json")
    assert created.status_code == 201 and created.json()["is_placeholder"] is False
    duplicate = admin.post(plans_url, body, format="json")
    assert duplicate.status_code == 400 and "code" in duplicate.json()["fields"]

    starter = next(p for p in listed if p["code"] == "starter")
    edited = admin.patch(
        f"{plans_url}/{starter['id']}",
        {"name": "Starter", "monthly_fee_paise": 149_900, "code": "basic"},
        format="json",
    ).json()
    assert (edited["name"], edited["code"], edited["is_placeholder"]) == (
        "Starter",
        "starter",
        False,
    )
    with db_context(platform=True):
        entry = AuditLog.objects.filter(object_type="billing.plan", action="update").latest(
            "created_at"
        )
    assert (
        entry.before["monthly_fee_paise"] == 199_900 and entry.after["monthly_fee_paise"] == 149_900
    )
    assert entry.tenant_id is None

    assert support.get(plans_url).status_code == 200
    assert support.patch(f"{plans_url}/{starter['id']}", {"name": "X"}).status_code == 403
    assert support.post(plans_url, body, format="json").status_code == 403


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"monthly_fee_paise": -1}, "monthly_fee_paise"),
        ({"max_active_subscribers": 0}, "max_active_subscribers"),
    ],
)
def test_plan_rules(admin, change, field):
    plan = Plan.objects.get(code="growth")
    response = admin.patch(f"/api/admin/plans/{plan.pk}", change, format="json")
    assert response.status_code == 400 and field in response.json()["fields"]


def test_portal_users_have_no_admin_billing(portal, client_tenant):
    api = portal("owner")
    assert api.get("/api/admin/plans").status_code == 403
    assert api.get(f"/api/admin/tenants/{client_tenant.pk}/subscription").status_code == 403


# --- price list -------------------------------------------------------------------------------


def test_price_list_entries(admin):
    url = "/api/admin/price-list"
    response = admin.post(
        url, {"channel": "whatsapp_utility", "price_paise": 90, "effective_from": str(TOMORROW)}
    )
    assert response.status_code == 201
    entry_id = response.json()["id"]
    assert (
        admin.post(
            url, {"channel": "whatsapp_utility", "price_paise": 95, "effective_from": str(TOMORROW)}
        ).json()["code"]
        == "price_exists"
    )
    backdated = admin.post(
        url,
        {
            "channel": "whatsapp_utility",
            "price_paise": 70,
            "effective_from": str(TODAY - dt.timedelta(days=1)),
        },
    )
    assert backdated.status_code == 400
    data = admin.get(url).json()
    assert {c["channel"]: c["price_paise"] for c in data["current"]}["whatsapp_utility"] == 85
    assert any(e["id"] == entry_id for e in data["entries"])
    assert admin.delete(f"{url}/{entry_id}").status_code == 204
    seeded = PriceList.objects.get(channel="whatsapp_utility", effective_from=dt.date(2026, 4, 1))
    assert admin.delete(f"{url}/{seeded.pk}").json()["code"] == "price_in_use"


# --- overrides --------------------------------------------------------------------------------


def override(admin, tenant, **body):
    return admin.post(f"/api/admin/tenants/{tenant.pk}/price-overrides", body, format="json")


def test_override_rules(admin, client_tenant):
    start = str(TOMORROW)
    first = override(admin, client_tenant, channel="voice_minute", price_paise=120, starts_on=start)
    assert first.status_code == 201 and first.json()["state"] == "scheduled"
    # Open-ended from tomorrow: anything later overlaps.
    later = override(
        admin,
        client_tenant,
        channel="voice_minute",
        price_paise=110,
        starts_on=str(TODAY + dt.timedelta(days=30)),
    )
    assert later.status_code == 409 and later.json()["code"] == "override_overlap"
    other_channel = override(
        admin, client_tenant, channel="whatsapp_utility", price_paise=70, starts_on=start
    )
    assert other_channel.status_code == 201
    backdated = override(
        admin,
        client_tenant,
        channel="whatsapp_service",
        price_paise=30,
        starts_on=str(TODAY - dt.timedelta(days=1)),
    )
    assert backdated.status_code == 400
    reversed_dates = override(
        admin,
        client_tenant,
        channel="whatsapp_service",
        price_paise=30,
        starts_on=str(TODAY + dt.timedelta(days=5)),
        ends_on=str(TODAY + dt.timedelta(days=4)),
    )
    assert reversed_dates.status_code == 400

    # Ending the first override makes room for a later one.
    url = f"/api/admin/tenants/{client_tenant.pk}/price-overrides/{first.json()['id']}"
    ended = admin.patch(url, {"ends_on": str(TODAY + dt.timedelta(days=10))}, format="json")
    assert ended.status_code == 200
    assert (
        override(
            admin,
            client_tenant,
            channel="voice_minute",
            price_paise=110,
            starts_on=str(TODAY + dt.timedelta(days=11)),
        ).status_code
        == 201
    )
    # A scheduled override can be deleted; a running one can't.
    assert admin.delete(url).status_code == 204
    running = override(
        admin, client_tenant, channel="whatsapp_service", price_paise=30, starts_on=str(TODAY)
    )
    running_url = f"/api/admin/tenants/{client_tenant.pk}/price-overrides/{running.json()['id']}"
    assert admin.delete(running_url).json()["code"] == "override_started"
    assert admin.get(f"/api/admin/tenants/{client_tenant.pk}/price-overrides").status_code == 200


def test_usage_after_an_override_starts_is_charged_at_the_override(
    admin, client_tenant, gas_template, monkeypatch
):
    override(
        admin,
        client_tenant,
        channel="whatsapp_utility",
        price_paise=60,
        starts_on=str(TOMORROW),
        ends_on=str(TOMORROW + dt.timedelta(days=6)),
    )
    billing.credit(client_tenant, 10_000, reason="adjustment")
    with tenant_context(client_tenant):
        subscriber = factories_subscriber(client_tenant)
        schedule = Schedule.objects.create(
            tenant=client_tenant,
            schedule_type=gas_template.schedule_types.get(key="cylinder_delivery"),
            subscriber=subscriber,
            booking_reference="BK-OVR",
            booking_date=TODAY,
        )
        attempts = [
            Attempt.objects.create(tenant=client_tenant, schedule=schedule, channel="whatsapp")
            for _ in range(3)
        ]
    billing.charge_attempt(attempts[0])  # today: list price
    on_day(monkeypatch, TOMORROW)
    billing.charge_attempt(attempts[1])  # the override's first day
    on_day(monkeypatch, TOMORROW + dt.timedelta(days=7))
    billing.charge_attempt(attempts[2])  # the day after it ends: list price again
    with tenant_context(client_tenant):
        costs = [Attempt.objects.get(pk=a.pk).cost_paise for a in attempts]
    assert costs == [85, 60, 85]
    assert billing.wallet_for(client_tenant).balance_paise == 10_000 - 230


def factories_subscriber(tenant):
    from apps.subscribers.models import Subscriber

    return Subscriber.objects.create(
        tenant=tenant,
        customer_name="Override Customer",
        mobile_number="+919847099999",
        external_ref="KL-OVR",
        whatsapp_consent=True,
        consent_date=TODAY,
        consent_source="paper_form",
    )


# --- plan changes (Workflow 3.8) --------------------------------------------------------------


def test_a_plan_change_scheduled_today_applies_only_on_the_next_billing_date(
    admin, client_tenant, owner, monkeypatch, django_capture_on_commit_callbacks
):
    url = f"/api/admin/tenants/{client_tenant.pk}/subscription"
    billing_date = subscription_for(client_tenant).next_billing_date
    assert billing_date > TODAY

    data = admin.post(f"{url}/plan-change", {"plan_code": "growth"}, format="json").json()
    sub = data["subscription"]
    assert sub["plan"]["code"] == "starter" and sub["pending_plan"]["code"] == "growth"
    assert sub["next_billing_date"] == str(billing_date)

    assert plans.apply_billing_date(client_tenant) == "not_due"  # today
    day_before = billing_date - dt.timedelta(days=1)
    assert plans.apply_billing_date(client_tenant, today=day_before) == "not_due"
    with tenant_context(client_tenant):
        assert Subscription.objects.get().plan.code == "starter"
        assert not Invoice.objects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        assert plans.apply_billing_date(client_tenant, today=billing_date) == "plan_changed"
    with tenant_context(client_tenant):
        subscription = Subscription.objects.select_related("plan").get()
        invoice = Invoice.objects.get()
    assert subscription.plan.code == "growth" and subscription.pending_plan is None
    assert subscription.current_period_start == billing_date
    assert subscription.next_billing_date > billing_date
    assert (invoice.kind, invoice.taxable_paise) == ("subscription", 499_900)
    assert "Growth (placeholder) plan" in invoice.lines[0]["description"]
    assert len(mail.outbox) == 1 and owner.email in mail.outbox[0].to
    assert "Growth (placeholder)" in mail.outbox[0].subject

    # Once per period: running again the same day changes nothing.
    assert plans.apply_billing_date(client_tenant, today=billing_date) == "not_due"
    with tenant_context(client_tenant):
        assert Invoice.objects.count() == 1


def test_renewal_without_a_change_rolls_the_period(client_tenant):
    subscription = subscription_for(client_tenant)
    assert (
        plans.apply_billing_date(client_tenant, today=subscription.next_billing_date) == "renewed"
    )
    with tenant_context(client_tenant):
        assert Invoice.objects.get().taxable_paise == 199_900


def test_pending_change_can_be_cancelled(admin, support, client_tenant):
    url = f"/api/admin/tenants/{client_tenant.pk}/subscription/plan-change"
    assert admin.delete(url).json()["code"] == "no_pending_change"
    assert admin.post(url, {"plan_code": "starter"}).status_code == 400  # current plan
    retired = Plan.objects.get(code="pro")
    retired.is_active = False
    retired.save()
    assert admin.post(url, {"plan_code": "pro"}).status_code == 400
    admin.post(url, {"plan_code": "growth"})
    assert support.post(url, {"plan_code": "starter"}).status_code == 403
    assert support.delete(url).status_code == 403
    data = admin.delete(url).json()
    assert data["subscription"]["pending_plan"] is None
    assert (
        plans.apply_billing_date(
            client_tenant, today=subscription_for(client_tenant).next_billing_date
        )
        == "renewed"
    )
    with tenant_context(client_tenant):
        assert Subscription.objects.get().plan.code == "starter"


def test_billing_view_for_support_staff(support, client_tenant, admin):
    override(admin, client_tenant, channel="voice_minute", price_paise=120, starts_on=str(TODAY))
    billing.record_top_up(client_tenant, 100_000, payment_reference="pay_admin_view")
    data = support.get(f"/api/admin/tenants/{client_tenant.pk}/subscription").json()
    assert data["wallet"]["balance_paise"] == 100_000 and data["wallet"]["can_send"] is True
    prices = {p["channel"]: p for p in data["prices"]}
    assert (prices["voice_minute"]["price_paise"], prices["voice_minute"]["source"]) == (
        120,
        "override",
    )
    assert prices["whatsapp_utility"]["source"] == "price_list"
    assert data["recent_transactions"][0]["kind"] == "credit"
    assert data["invoices"][0]["total_paise"] == 118_000
    assert data["mandate"] is None and data["subscription"]["plan"]["code"] == "starter"


def test_new_clients_start_on_the_cheapest_plan(gas_template, super_admin):
    from apps.platform_admin.services import create_client

    tenant = create_client(
        {
            "industry_template": gas_template,
            "name": "New Plan Gas",
            "legal_name": "New Plan Gas LLP",
            "address_line1": "Main Road",
            "city": "Kochi",
            "state": "KL",
            "pin_code": "682016",
            "contact_name": "Office",
            "contact_phone": "+919847000001",
            "contact_email": "office@newplan.test",
            "owner_name": "Owner",
            "owner_email": "owner@newplan.test",
        },
        super_admin,
    )
    with tenant_context(tenant):
        assert Subscription.objects.get(tenant=tenant).plan.code == "starter"


def test_billing_dates_task(client_tenant, monkeypatch):
    from apps.billing.tasks import apply_billing_dates

    other = factories.make_tenant("Other Gas")
    subscription_for(client_tenant)
    subscription_for(other)
    assert apply_billing_dates() == 0
    on_day(monkeypatch, subscription_for(client_tenant).next_billing_date)
    assert apply_billing_dates() == 2
    assert apply_billing_dates() == 0
    with tenant_context(other):
        assert PriceOverride.objects.count() == 0

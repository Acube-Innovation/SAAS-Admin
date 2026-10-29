"""Wallet engine (portal spec 2.9, Workflow 3.4): debits, credits, low balance, holds, invoices."""

import datetime as dt
import threading

import pytest
from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.billing import services as billing
from apps.billing.models import Invoice, PriceList, Wallet, WalletTransaction
from apps.billing.pdf import amount_in_words, rupees
from apps.core.tenancy import db_context, tenant_context
from apps.schedules.models import Attempt, Schedule, ScheduleEvent
from tests import factories

pytestmark = pytest.mark.django_db
T = WalletTransaction
TODAY = timezone.localdate()


def balance(tenant):
    with tenant_context(tenant):
        return Wallet.objects.get(tenant=tenant).balance_paise


def ledger(tenant):
    with tenant_context(tenant):
        return list(T.objects.filter(tenant=tenant).order_by("created_at"))


def booking(tenant, gas_template, ref="BK1", **extra):
    with tenant_context(tenant):
        subscriber = factories_subscriber(tenant, ref)
        return Schedule.objects.create(
            tenant=tenant,
            schedule_type=gas_template.schedule_types.get(key="cylinder_delivery"),
            subscriber=subscriber,
            booking_reference=ref,
            booking_date=TODAY,
            scheduled_date=TODAY + dt.timedelta(days=1),
            **extra,
        )


def factories_subscriber(tenant, ref):
    from apps.subscribers.models import Subscriber

    return Subscriber.objects.create(
        tenant=tenant,
        customer_name=f"Customer {ref}",
        mobile_number=f"+9198470{factories.next_id():05d}",
        external_ref=f"KL-{ref}",
        whatsapp_consent=True,
        consent_date=TODAY,
        consent_source="paper_form",
    )


# --- prices -----------------------------------------------------------------------------------


def test_price_list_uses_the_latest_entry_in_effect(client_tenant):
    assert billing.price_for(client_tenant, "whatsapp_utility") == 85  # seeded default
    PriceList.objects.create(
        channel="whatsapp_utility", price_paise=90, effective_from=TODAY - dt.timedelta(days=1)
    )
    PriceList.objects.create(
        channel="whatsapp_utility", price_paise=99, effective_from=TODAY + dt.timedelta(days=5)
    )
    assert billing.price_for(client_tenant, "whatsapp_utility") == 90
    assert billing.price_for(client_tenant, "whatsapp_utility", TODAY + dt.timedelta(days=5)) == 99


# --- debits and credits -----------------------------------------------------------------------


def test_debits_and_credits_keep_a_checkable_ledger(client_tenant):
    billing.credit(client_tenant, 1000, reason="adjustment", idempotency_key="adj-1")
    billing.debit(client_tenant, 85, reference="attempt:a", idempotency_key="attempt:a")
    billing.debit(client_tenant, 150, reference="attempt:b", idempotency_key="attempt:b")
    assert balance(client_tenant) == 765
    entries = ledger(client_tenant)
    assert [(e.kind, e.amount_paise, e.balance_after_paise) for e in entries] == [
        ("credit", 1000, 1000),
        ("debit", 85, 915),
        ("debit", 150, 765),
    ]
    with db_context(platform=True):
        assert AuditLog.objects.filter(object_type="billing.wallet", tenant=client_tenant).exists()


def test_repeated_keys_charge_or_credit_once(client_tenant):
    first = billing.credit(client_tenant, 1000, reason="adjustment", idempotency_key="pay-1")
    again = billing.credit(client_tenant, 1000, reason="adjustment", idempotency_key="pay-1")
    assert first.pk == again.pk
    billing.debit(client_tenant, 85, idempotency_key="attempt:x")
    billing.debit(client_tenant, 85, idempotency_key="attempt:x")
    assert balance(client_tenant) == 915 and len(ledger(client_tenant)) == 2


def test_a_debit_larger_than_the_balance_changes_nothing_and_pauses(client_tenant):
    billing.credit(client_tenant, 100, reason="adjustment")
    with pytest.raises(billing.InsufficientBalance):
        billing.debit(client_tenant, 150)
    assert balance(client_tenant) == 100 and len(ledger(client_tenant)) == 1
    with tenant_context(client_tenant):
        assert Wallet.objects.get(tenant=client_tenant).paused_at is not None  # committed


def test_sending_pauses_below_the_price_of_one_reminder(client_tenant):
    assert not client_tenant.can_send()  # empty wallet
    billing.credit(client_tenant, 170, reason="adjustment")
    assert client_tenant.can_send()
    billing.debit(client_tenant, 85)
    billing.debit(client_tenant, 85)
    assert balance(client_tenant) == 0 and not client_tenant.can_send()
    with tenant_context(client_tenant):
        assert Wallet.objects.get(tenant=client_tenant).paused_at is not None


def test_the_database_refuses_a_negative_balance(client_tenant):
    billing.credit(client_tenant, 100, reason="adjustment")
    with tenant_context(client_tenant), pytest.raises(IntegrityError), transaction.atomic():
        Wallet.objects.filter(tenant=client_tenant).update(balance_paise=-1)


@pytest.mark.django_db(transaction=True)
def test_parallel_debits_never_take_the_balance_below_zero(gas_template):
    """20 debits of 85 paise race for 1,000 paise: exactly 11 succeed and 65 paise remain."""
    tenant = factories.make_tenant("Race Gas Agency")
    from apps.industry.services import assign_template

    assign_template(tenant, gas_template)
    tenant.save()
    billing.credit(tenant, 1000, reason="adjustment")
    start = threading.Barrier(20)
    results = []

    def worker(n):
        try:
            start.wait()
            billing.debit(tenant, 85, reference=f"attempt:{n}", idempotency_key=f"attempt:{n}")
            results.append("ok")
        except billing.InsufficientBalance:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("ok") == 11 and results.count("refused") == 9
    assert balance(tenant) == 1000 - 11 * 85 == 65
    debits = [e for e in ledger(tenant) if e.kind == "debit"]
    assert sorted((e.balance_after_paise for e in debits), reverse=True) == [
        1000 - 85 * n for n in range(1, 12)
    ]
    assert min(e.balance_after_paise for e in debits) >= 0


# --- low balance ------------------------------------------------------------------------------


def backdate(entries, days_ago):
    for entry, days in zip(entries, days_ago, strict=True):
        entry.created_at = timezone.now() - dt.timedelta(days=days)
    with tenant_context(entries[0].tenant_id):
        T.objects.bulk_update(entries, ["created_at"])


def test_low_balance_level_is_1000_rupees_until_14_days_of_history(client_tenant):
    assert billing.low_balance_level(client_tenant) == 100_000
    billing.credit(client_tenant, 50_000, reason="adjustment")
    entries = [billing.debit(client_tenant, 700, idempotency_key=f"d{n}") for n in range(10)]
    backdate(entries, range(10, 0, -1))  # 10 days of history
    assert billing.low_balance_level(client_tenant) == 100_000


def test_low_balance_level_is_three_times_average_daily_usage(client_tenant):
    billing.credit(client_tenant, 500_000, reason="adjustment")
    # 20 days of history; the last 14 full days used 1,400 paise a day; today's use doesn't count.
    entries = [billing.debit(client_tenant, 1400, idempotency_key=f"d{n}") for n in range(20)]
    backdate(entries, range(20, 0, -1))
    billing.debit(client_tenant, 99_999, idempotency_key="today")
    assert billing.low_balance_level(client_tenant) == 3 * 1400 == 4200


def test_falling_below_the_level_alerts_once(
    client_tenant, owner, django_capture_on_commit_callbacks
):
    received = []
    billing.wallet_low_balance.connect(
        lambda **kw: received.append(kw["level_paise"]), weak=False, dispatch_uid="test-low"
    )
    billing.credit(client_tenant, 100_100, reason="adjustment")
    with django_capture_on_commit_callbacks(execute=True):
        billing.debit(client_tenant, 85)  # 100,015 paise: still above ₹1,000
        billing.debit(client_tenant, 85)  # below -> alert
        billing.debit(client_tenant, 85)  # still below -> no second alert
    assert received == [100_000]
    assert len(mail.outbox) == 1 and owner.email in mail.outbox[0].to
    assert "₹998.45" in mail.outbox[0].body  # the balance when the email is sent (after commit)
    billing.credit(client_tenant, 50_000, reason="adjustment")
    with tenant_context(client_tenant):
        assert Wallet.objects.get(tenant=client_tenant).low_balance_since is None
    billing.wallet_low_balance.disconnect(dispatch_uid="test-low")  # keep the app's receivers


def test_home_shows_wallet_alerts(portal, client_tenant):
    api = portal("owner")
    codes = {a["code"] for a in api.get("/api/portal/home").json()["alerts"]}
    assert "wallet_empty" in codes
    billing.credit(client_tenant, 50_000, reason="adjustment")
    codes = {a["code"] for a in api.get("/api/portal/home").json()["alerts"]}
    assert "wallet_low" in codes and "wallet_empty" not in codes
    billing.credit(client_tenant, 100_000, reason="adjustment")
    codes = {a["code"] for a in api.get("/api/portal/home").json()["alerts"]}
    assert not {"wallet_low", "wallet_empty"} & codes


# --- held reminders (Workflow 3.4) ------------------------------------------------------------


def test_held_reminders_are_skipped_or_released_after_a_credit(client_tenant, gas_template):
    now = timezone.now()
    overdue = booking(
        client_tenant, gas_template, "BK1", reminder_due_at=now - dt.timedelta(hours=2)
    )
    later = booking(client_tenant, gas_template, "BK2", reminder_due_at=now + dt.timedelta(hours=3))
    assert not client_tenant.can_send()
    with tenant_context(client_tenant):
        billing.hold_reminder(overdue)
        billing.hold_reminder(later)
        billing.hold_reminder(later)  # holding twice is harmless
        assert Schedule.objects.filter(reminder_held_at__isnull=False).count() == 2

    billing.credit(client_tenant, 10_000, reason="adjustment")

    with tenant_context(client_tenant):
        overdue.refresh_from_db()
        later.refresh_from_db()
        assert (overdue.outcome, overdue.status, overdue.reminder_held_at) == (
            "skipped",
            "closed",
            None,
        )
        assert later.outcome == "" and later.reminder_held_at is None
        kinds = set(ScheduleEvent.objects.values_list("schedule__booking_reference", "kind"))
    assert {("BK1", "reminder_held"), ("BK1", "reminder_skipped"), ("BK1", "outcome_set")} <= kinds
    assert ("BK2", "reminder_released") in kinds


def test_a_credit_that_does_not_cover_a_reminder_keeps_them_held(client_tenant, gas_template):
    held = booking(
        client_tenant, gas_template, reminder_due_at=timezone.now() - dt.timedelta(hours=1)
    )
    with tenant_context(client_tenant):
        billing.hold_reminder(held)
    billing.credit(client_tenant, 50, reason="adjustment")  # below one message (85 paise)
    with tenant_context(client_tenant):
        held.refresh_from_db()
    assert held.reminder_held_at is not None and held.outcome == ""


def test_charge_attempt_prices_by_channel_once(client_tenant, gas_template):
    billing.credit(client_tenant, 10_000, reason="adjustment")
    schedule = booking(client_tenant, gas_template)
    with tenant_context(client_tenant):
        message = Attempt.objects.create(
            tenant=client_tenant, schedule=schedule, channel="whatsapp"
        )
        call = Attempt.objects.create(tenant=client_tenant, schedule=schedule, channel="voice")
    billing.charge_attempt(message)
    billing.charge_attempt(message)  # retried by the engine: charged once
    billing.charge_attempt(call, minutes=3)
    with tenant_context(client_tenant):
        message.refresh_from_db()
        call.refresh_from_db()
    assert (message.cost_paise, call.cost_paise) == (85, 450)
    assert balance(client_tenant) == 10_000 - 85 - 450


# --- invoices ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "year"),
    [
        (dt.date(2026, 3, 31), "2025-26"),
        (dt.date(2026, 4, 1), "2026-27"),
        (dt.date(2026, 10, 28), "2026-27"),
        (dt.date(2099, 12, 31), "2099-00"),
    ],
)
def test_financial_year(day, year):
    assert billing.financial_year(day) == year


def test_invoice_numbers_run_per_financial_year(client_tenant):
    def issue(day):
        issued_at = timezone.make_aware(dt.datetime.combine(day, dt.time(12)))
        return billing.issue_invoice(
            client_tenant,
            "top_up",
            [{"description": "Top-up", "unit_paise": 100_000}],
            issued_at=issued_at,
        ).number

    assert issue(dt.date(2026, 3, 30)) == "SC/2025-26/000001"
    assert issue(dt.date(2026, 4, 1)) == "SC/2026-27/000001"
    assert issue(dt.date(2026, 10, 28)) == "SC/2026-27/000002"
    assert issue(dt.date(2026, 3, 31)) == "SC/2025-26/000002"


def test_gst_within_the_state_and_across_states(client_tenant, settings):
    settings.SELLER_STATE = "KL"
    within_state = billing.issue_invoice(
        client_tenant, "top_up", [{"description": "Top-up", "unit_paise": 100_000}]
    )
    assert (within_state.cgst_paise, within_state.sgst_paise, within_state.igst_paise) == (
        9000,
        9000,
        0,
    )
    assert within_state.total_paise == 118_000
    settings.SELLER_STATE = "KA"
    other = billing.issue_invoice(
        client_tenant, "top_up", [{"description": "Top-up", "unit_paise": 100_001}]
    )
    assert (other.cgst_paise, other.sgst_paise, other.igst_paise) == (0, 0, 18000)
    assert other.total_paise == 118_001
    settings.SELLER_STATE = "KL"
    odd = billing.gst_split(1_250, "KL")  # 112.5 paise each -> rounded half up
    assert (odd["cgst_paise"], odd["sgst_paise"]) == (113, 113)


def test_invoice_pdf_renders_the_tax_invoice(client_tenant, settings, monkeypatch):
    from reportlab import rl_config

    # Readable text in the PDF: standard font (no subsetting) and no stream compression.
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    settings.INVOICE_FONT_PATH = "/no/such/font.ttf"
    settings.SELLER_LEGAL_NAME = "Acube Test Pvt Ltd"
    settings.SELLER_GSTIN = "32AAAAA0000A1Z5"
    invoice = billing.issue_invoice(
        client_tenant,
        "top_up",
        [{"description": "Wallet top-up (prepaid reminder credit)", "unit_paise": 100_000}],
        payment_reference="pay_test_1",
    )
    with tenant_context(client_tenant):
        invoice.refresh_from_db()
        content = invoice.pdf.read()
    assert content.startswith(b"%PDF") and content.count(b"/Type /Page\n") == 1
    assert invoice.pdf.name.startswith(f"tenants/{client_tenant.pk}/invoices/")
    for text in [
        b"TAX INVOICE",
        invoice.number.encode(),
        b"Acube Test Pvt Ltd",
        b"32AAAAA0000A1Z5",
        client_tenant.legal_name.encode(),
        rb"Wallet top-up \(prepaid reminder credit\)",  # PDF escapes parentheses
        b"998319",
        b"CGST 9%",
        b"SGST 9%",
        b"Rs.1,000.00",
        b"Rs.90.00",
        b"Rs.1,180.00",
        b"Rupees One Thousand One Hundred Eighty only",
        b"pay_test_1",
    ]:
        assert text in content, text


def test_money_formatting():
    assert rupees(123_456_789) == "₹12,34,567.89"
    assert rupees(118_000) == "₹1,180.00" and rupees(5, "Rs.") == "Rs.0.05"
    assert amount_in_words(118_000) == "Rupees One Thousand One Hundred Eighty only"
    assert amount_in_words(12_345_678_950) == (
        "Rupees Twelve Crore Thirty Four Lakh Fifty Six Thousand Seven Hundred Eighty Nine "
        "and Fifty Paise only"
    )


def test_top_up_invoices_with_gst_on_top_and_credits_once(client_tenant):
    first = billing.record_top_up(client_tenant, 100_000, payment_reference="pay_1")
    again = billing.record_top_up(client_tenant, 100_000, payment_reference="pay_1")
    assert first.pk == again.pk and balance(client_tenant) == 100_000  # full value credited
    with tenant_context(client_tenant):
        invoice = Invoice.objects.get()
    assert (invoice.taxable_paise, invoice.total_paise) == (100_000, 118_000)
    assert first.invoice_id == invoice.pk and invoice.payment_reference == "pay_1"


# --- portal API -------------------------------------------------------------------------------


def test_wallet_api_for_owner_and_manager(portal, client_tenant):
    billing.record_top_up(client_tenant, 100_000, payment_reference="pay_9")
    billing.debit(client_tenant, 85, channel="whatsapp_utility", idempotency_key="attempt:z")
    api = portal("manager")
    wallet = api.get("/api/portal/wallet").json()
    assert wallet["balance_paise"] == 99_915 and wallet["can_send"] is True
    assert wallet["low_balance_level_paise"] == 100_000 and wallet["is_low"] is True
    assert {p["channel"]: p["price_paise"] for p in wallet["prices"]}["voice_minute"] == 150
    txns = api.get("/api/portal/wallet/transactions").json()["results"]
    assert [t["kind"] for t in txns] == ["debit", "credit"]
    assert txns[1]["invoice_number"].startswith("SC/")
    invoices = api.get("/api/portal/invoices").json()["results"]
    assert invoices[0]["total_paise"] == 118_000
    link = api.get(f"/api/portal/invoices/{invoices[0]['id']}/pdf").json()
    assert link["expires_in"] == 300 and link["url"]


@pytest.mark.parametrize(
    "url", ["/api/portal/wallet", "/api/portal/invoices", "/api/portal/wallet/transactions"]
)
def test_office_staff_have_no_billing(portal, url):
    assert portal("office_staff").get(url).status_code == 403


def test_billing_is_isolated_per_agency(portal, client_tenant):
    other = factories.make_tenant("Other Agency")
    billing.record_top_up(other, 100_000, payment_reference="pay_other")
    api = portal("owner")
    assert api.get("/api/portal/invoices").json()["count"] == 0
    with tenant_context(other):
        invoice = Invoice.objects.get()
    assert api.get(f"/api/portal/invoices/{invoice.pk}/pdf").status_code == 404
    assert api.get("/api/portal/wallet").json()["balance_paise"] == 0


def test_wallet_top_up_command_is_for_local_development(client_tenant):
    import io

    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError, match="local development"):
        call_command("wallet_top_up", client_tenant.name, "1000")
    out = io.StringIO()
    call_command("wallet_top_up", client_tenant.name, "1000", "--allow-non-debug", stdout=out)
    assert "Credited ₹1,000.00" in out.getvalue() and "invoice SC/" in out.getvalue()
    assert balance(client_tenant) == 100_000
    with pytest.raises(CommandError, match="No tenant"):
        call_command("wallet_top_up", "Nobody", "1000", "--allow-non-debug")

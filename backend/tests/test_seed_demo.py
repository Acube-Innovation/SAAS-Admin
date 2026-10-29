"""seed_demo: a complete, consistent demo gas agency for UI work."""

import io

import pytest
from django.core.management import CommandError, call_command
from django.db.models import Count
from django.utils import timezone

from apps.accounts.models import Membership
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context
from apps.schedules.models import Attempt, Outcome, Response, Schedule, ScheduleEvent
from apps.subscribers.models import Area, Subscriber

pytestmark = pytest.mark.django_db
PASSWORD = "demo-password-for-tests-1\n"


def seed(*args, stdin=PASSWORD):
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.stdin", io.StringIO(stdin))
        call_command("seed_demo", "--allow-non-debug", "--password-stdin", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def demo(gas_template):
    output = seed("--subscribers", "200")
    tenant = Tenant.objects.get(name="Demo Gas Agency")
    return tenant, output


def test_creates_a_working_agency(demo):
    tenant, output = demo
    assert tenant.industry_template.key == "gas_agency"
    assert "subscribers: 200" in output
    assert "demo-password" not in output  # a password given on stdin is never printed
    with tenant_context(tenant):
        roles = set(
            Membership.objects.filter(tenant=tenant, status="active").values_list("role", flat=True)
        )
        assert roles == {"owner", "manager", "office_staff"}
        assert Area.objects.count() == 6
        assert Area.objects.exclude(default_staff=None).count() == 6
        assert Subscriber.objects.count() == 200
        assert Schedule.objects.count() > 100
        assert Attempt.objects.filter(channel="whatsapp").exists()
        assert Attempt.objects.filter(channel="voice").exists()
        assert Response.objects.exclude(transcript="").exists()


def test_data_is_consistent(demo):
    tenant, _output = demo
    with tenant_context(tenant):
        # Only subscribers who may be messaged got reminders.
        for schedule in (
            Schedule.objects.filter(attempts__isnull=False).distinct().select_related("subscriber")
        ):
            assert schedule.subscriber.can_be_messaged()
        # Every outcome has the matching history.
        assert not Schedule.objects.filter(outcome="", status="closed").exists()
        for outcome in [Outcome.CONFIRMED, Outcome.NOT_AVAILABLE, Outcome.RESCHEDULE]:
            schedules = Schedule.objects.filter(outcome=outcome)
            assert schedules.exists(), outcome
            assert (
                not schedules.filter(attempts__responses__isnull=True, attempts__channel="whatsapp")
                .exclude(attempts__channel="voice")
                .exists()
            )
        # "Change date" without a picked day waits in the follow-up queue (Workflow 3.2).
        waiting = Schedule.objects.filter(outcome="reschedule", new_date_requested=None)
        assert waiting.exists()
        assert not waiting.filter(follow_ups__isnull=True).exists()
        # Confirmed deliveries have a job with the area's default staff member.
        confirmed = Schedule.objects.filter(outcome="confirmed").exclude(scheduled_date=None)
        assert not confirmed.filter(jobs__isnull=True).exists()
        with_area = confirmed.exclude(subscriber__area=None).select_related("subscriber__area")
        for schedule in with_area[:30]:
            assert (
                schedule.jobs.get().assigned_staff_id == schedule.subscriber.area.default_staff_id
            )
        # Every other follow-up outcome has an item; confirmed ones never do.
        for outcome in ["not_available", "no_response", "failed", "needs_review"]:
            assert not Schedule.objects.filter(outcome=outcome, follow_ups__isnull=True).exists(), (
                outcome
            )
        assert not Schedule.objects.filter(outcome="confirmed", follow_ups__isnull=False).exists()
        failed = Schedule.objects.filter(outcome="failed")
        assert failed.exists() and not failed.exclude(attempts__delivery_status="failed").exists()
        assert (
            not Schedule.objects.filter(scheduled_date=None)
            .exclude(outcome="")
            .exclude(status="cancelled")
            .exists()
        )
        # Some bookings have no delivery date yet (the "No date yet" filter has something to show).
        assert Schedule.objects.filter(scheduled_date=None).exists()
        # Timelines start with "created" and are in time order.
        per_schedule = ScheduleEvent.objects.values("schedule").annotate(n=Count("id"))
        assert per_schedule.count() == Schedule.objects.count()
        for schedule in Schedule.objects.all()[:50]:
            kinds = list(schedule.events.values_list("kind", flat=True))
            assert kinds[0] == "created"
            times = list(schedule.events.values_list("occurred_at", flat=True))
            assert times == sorted(times)


def test_is_deterministic(gas_template):
    seed("--name", "Demo A", "--subscribers", "30")
    seed("--name", "Demo B", "--subscribers", "30")
    names = []
    for name in ["Demo A", "Demo B"]:
        with tenant_context(Tenant.objects.get(name=name)):
            names.append(
                list(
                    Subscriber.objects.order_by("external_ref").values_list(
                        "customer_name", "mobile_number"
                    )
                )
            )
    assert names[0] == names[1]


def test_data_is_visible_only_inside_its_tenant(demo, tenant):
    with tenant_context(tenant):
        assert Subscriber.objects.count() == 0 and Schedule.objects.count() == 0


def test_refuses_without_debug_or_twice(demo):
    with pytest.raises(CommandError, match="local development"):
        call_command("seed_demo")
    with pytest.raises(CommandError, match="already exists"):
        seed()


def test_weak_password_is_refused(gas_template):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        seed("--name", "Weak", stdin="12345678\n")


def test_outcomes_come_after_the_answers_they_are_based_on(demo):
    tenant, _output = demo
    with tenant_context(tenant):
        for schedule in Schedule.objects.exclude(outcome="").prefetch_related(
            "attempts__responses", "events"
        ):
            decided = [e.occurred_at for e in schedule.events.all() if e.kind == "outcome_set"]
            if not decided:
                continue
            replies = [r.received_at for a in schedule.attempts.all() for r in a.responses.all()]
            sends = [a.sent_at for a in schedule.attempts.all() if a.sent_at]
            assert all(decided[-1] >= t for t in replies + sends), schedule.booking_reference


def test_nothing_happens_in_the_future(demo):
    tenant, _output = demo
    now = timezone.now()
    with tenant_context(tenant):
        assert not Attempt.objects.filter(sent_at__gt=now).exists()
        assert not Response.objects.filter(received_at__gt=now).exists()
        assert not ScheduleEvent.objects.filter(occurred_at__gt=now).exists()


def test_messages_and_voice_content(demo):
    from apps.change_requests.models import ChangeRequest
    from apps.messaging_templates.models import VoiceScriptVersion, WhatsAppTemplateVersion
    from apps.messaging_templates.validation import validate_content

    tenant, output = demo
    assert "template versions: 3" in output
    with tenant_context(tenant):
        versions = list(WhatsAppTemplateVersion.objects.select_related("template__schedule_type"))
        assert sorted((v.template.language, v.version, v.status, v.is_live) for v in versions) == [
            ("en", 1, "approved", True),
            ("en", 2, "sent_to_client", False),
            ("ml", 1, "approved", True),
        ]
        for version in versions:  # the demo content passes the editor's rules (spec 2.7)
            validate_content(
                schedule_type=version.template.schedule_type,
                header_text=version.header_text,
                body=version.body,
                footer=version.footer,
                buttons=version.buttons,
                sample_values=version.sample_values,
            )
        assert VoiceScriptVersion.objects.filter(status="live").count() == 1
        assert ChangeRequest.objects.get().status == "open"


def test_wallet_matches_the_communication_log(demo):
    from django.db.models import Sum

    from apps.billing.models import Invoice, Wallet, WalletTransaction

    tenant, output = demo
    with tenant_context(tenant):
        spent = Attempt.objects.aggregate(total=Sum("cost_paise"))["total"]
        wallet = Wallet.objects.get(tenant=tenant)
        assert wallet.balance_paise == 500_000 - spent
        debits = WalletTransaction.objects.filter(kind="debit")
        assert debits.count() == Attempt.objects.filter(cost_paise__gt=0).count()
        assert debits.order_by("-created_at").first().balance_after_paise == wallet.balance_paise
        invoice = Invoice.objects.get()
        assert (invoice.taxable_paise, invoice.total_paise) == (500_000, 590_000)
    assert f"wallet balance (paise): {wallet.balance_paise}" in output

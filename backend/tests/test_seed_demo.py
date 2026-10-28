"""seed_demo: a complete, consistent demo gas agency for UI work."""

import io

import pytest
from django.core.management import CommandError, call_command
from django.db.models import Count

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
        assert not Schedule.objects.filter(outcome="reschedule", new_date_requested=None).exists()
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

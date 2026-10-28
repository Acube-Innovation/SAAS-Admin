"""
Demo data for UI work: a gas agency with areas, staff, subscribers, bookings, WhatsApp messages,
voice calls, replies and outcomes. Deterministic (same --seed, same data). Local use only.

    python manage.py seed_demo          # 300 subscribers; a password is generated and printed
    python manage.py seed_demo --subscribers 2000 --password-stdin < pw.txt
"""

import datetime as dt
import random
import secrets
import sys
import uuid
import zoneinfo

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership, TenantRole, User
from apps.audit.models import Action
from apps.audit.services import write_audit
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context
from apps.industry.models import IndustryTemplate
from apps.industry.seed import seed_gas_agency
from apps.industry.services import assign_template
from apps.jobs.models import FollowUpItem, Job, StaffMember
from apps.schedules.models import Attempt, Outcome, Response, Schedule, ScheduleEvent
from apps.schedules.services import FOLLOW_UP_OUTCOMES, plan_job
from apps.subscribers.models import Area, Subscriber

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

AREAS = [
    ("Edappally", "EDP"),
    ("Kakkanad", "KKD"),
    ("Vyttila", "VYT"),
    ("Palarivattom", "PLV"),
    ("Kaloor", "KLR"),
    ("Fort Kochi", "FKC"),
]
STAFF = ["Biju K", "Shaji P", "Rajesh M", "Anoop V", "Suresh T", "Manoj R", "Prakash N"]
FIRST = [
    "Anil",
    "Sreeja",
    "Rahul",
    "Divya",
    "Jose",
    "Fathima",
    "Vinod",
    "Lakshmi",
    "Arun",
    "Meera",
    "Thomas",
    "Aysha",
    "Sajeev",
    "Anjali",
    "Muhammed",
    "Reshma",
    "Gopakumar",
    "Sindhu",
    "Joseph",
    "Nisha",
    "Hari",
    "Bindu",
    "Shibu",
    "Remya",
    "Abdul",
    "Deepa",
    "Varghese",
    "Sneha",
    "Ramesh",
]
LAST = [
    "Nair",
    "Menon",
    "Kurian",
    "Thomas",
    "Pillai",
    "Varghese",
    "Kumar",
    "Joseph",
    "Rahman",
    "Krishnan",
    "Mathew",
    "Das",
    "Haris",
    "George",
    "Panicker",
    "Iyer",
    "Antony",
    "Sebastian",
]
STREETS = [
    "MG Road",
    "Palace Road",
    "Church Road",
    "NH Bypass",
    "Seaport-Airport Road",
    "Market Road",
]
NEEDS_REVIEW_TEXT = [
    "ഞാൻ വൈകിട്ട് 5 മണിക്ക് ശേഷം മാത്രമേ വീട്ടിൽ ഉണ്ടാകൂ",
    "Please deliver to my neighbour, house no. 14",
    "Can you come before 9 am?",
    "Last time the cylinder was leaking, please check",
]
CALL_TRANSCRIPTS = {
    Outcome.CONFIRMED: (
        "Agent: Will someone be home tomorrow to receive the cylinder? "
        "Customer: Yes, I will be there."
    ),
    Outcome.NOT_AVAILABLE: "Agent: Will someone be home tomorrow? Customer: ഇല്ല, നാളെ ഞങ്ങൾ പുറത്താണ്.",
    Outcome.RESCHEDULE: (
        "Agent: Will someone be home tomorrow? Customer: Can you come on Saturday instead?"
    ),
}
# Shares of outcomes for bookings that were reminded.
OUTCOME_WEIGHTS = [
    (Outcome.CONFIRMED, 60),
    (Outcome.RESCHEDULE, 9),
    (Outcome.NOT_AVAILABLE, 8),
    (Outcome.NEEDS_REVIEW, 5),
    (Outcome.NO_RESPONSE, 11),
    (Outcome.FAILED, 4),
    (Outcome.OPTED_OUT, 1),
    (None, 2),  # still waiting for a reply
]
BUTTONS = {
    Outcome.CONFIRMED: ("yes_available", "Yes, available"),
    Outcome.NOT_AVAILABLE: ("not_available", "Not available"),
    Outcome.RESCHEDULE: ("change_date", "Change date"),
}
WHATSAPP_COST_PAISE = 15
VOICE_COST_PAISE = 150


class Command(BaseCommand):
    help = "Create a demo gas agency with subscribers, bookings, messages, calls and outcomes."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Demo Gas Agency", help="Tenant display name.")
        parser.add_argument("--email-domain", default="demo-gas.test")
        parser.add_argument("--subscribers", type=int, default=300)
        parser.add_argument("--seed", type=int, default=2026)
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the demo users' password from stdin (otherwise one is generated).",
        )
        parser.add_argument(
            "--allow-non-debug",
            action="store_true",
            help="Run even when DEBUG is off (never on production data).",
        )

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["allow_non_debug"]:
            raise CommandError("seed_demo is for local development (DEBUG=True).")
        if opts["subscribers"] < 1:
            raise CommandError("--subscribers must be at least 1.")
        if Tenant.objects.filter(name__iexact=opts["name"]).exists():
            raise CommandError(f"A tenant named {opts['name']!r} already exists. Use --name.")

        generated = not opts["password_stdin"]
        password = secrets.token_urlsafe(12) if generated else sys.stdin.readline().rstrip("\n")
        password_validation.validate_password(password)

        rng = random.Random(opts["seed"])  # noqa: S311 (reproducible demo data, not secrets)
        template = IndustryTemplate.objects.filter(key="gas_agency").first() or seed_gas_agency()
        with transaction.atomic():
            tenant = self.create_tenant(opts["name"], template)
            users = self.create_users(tenant, opts["email_domain"], password)
            with tenant_context(tenant):
                counts = self.create_data(tenant, template, rng, opts["subscribers"])
            write_audit(
                Action.CREATE,
                tenant,
                after={"seed_demo": counts, "users": [u.email for u in users]},
                tenant=tenant,
            )

        self.stdout.write(self.style.SUCCESS(f"Created {tenant.name} ({tenant.pk})"))
        for key, value in counts.items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write("Users (owner, manager, office staff; set up 2FA on first sign-in):")
        for user in users:
            self.stdout.write(f"  {user.email}")
        if generated:
            self.stdout.write(f"Password for all three: {password}")

    def create_tenant(self, name, template):
        tenant = Tenant(
            name=name,
            legal_name=f"{name} Pvt Ltd",
            address_line1="Door 41/2101, Banerji Road",
            city="Kochi",
            state="KL",
            pin_code="682018",
            contact_name="Office",
            contact_phone="+919847000100",
            contact_email="office@demo-gas.test",
            default_language="ml",
        )
        assign_template(tenant, template)
        tenant.save()
        return tenant

    def create_users(self, tenant, domain, password):
        slug = tenant.name.lower().replace(" ", "-")
        users = []
        for role, label in [
            (TenantRole.OWNER, "owner"),
            (TenantRole.MANAGER, "manager"),
            (TenantRole.OFFICE_STAFF, "staff"),
        ]:
            email = f"{label}@{slug}.{domain}" if slug != "demo-gas-agency" else f"{label}@{domain}"
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                user = User.objects.create_user(email, password, full_name=f"Demo {label.title()}")
            with tenant_context(tenant):
                Membership.objects.create(
                    tenant=tenant, user=user, role=role, status=Membership.Status.ACTIVE
                )
            users.append(user)
        return users

    def create_data(self, tenant, template, rng, count):
        staff = StaffMember.objects.bulk_create(
            [StaffMember(tenant=tenant, name=name, phone=self.mobile(rng)) for name in STAFF]
        )
        areas = Area.objects.bulk_create(
            [
                Area(tenant=tenant, name=name, code=code, default_staff=staff[i % len(staff)])
                for i, (name, code) in enumerate(AREAS)
            ]
        )
        today = timezone.localdate()
        subscribers = []
        for n in range(count):
            consent = rng.random() < 0.9
            opted_out = consent and rng.random() < 0.02
            subscribers.append(
                Subscriber(
                    tenant=tenant,
                    customer_name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                    mobile_number=self.mobile(rng),
                    alternate_number=self.mobile(rng) if rng.random() < 0.2 else "",
                    external_ref=f"KL0{7_000_000 + n * 37}",
                    preferred_language="en" if rng.random() < 0.25 else "ml",
                    address=f"{rng.randint(1, 250)}/{rng.randint(100, 999)}, {rng.choice(STREETS)}",
                    area=None if rng.random() < 0.03 else rng.choice(areas),
                    whatsapp_consent=consent,
                    consent_date=today - dt.timedelta(days=rng.randint(30, 400))
                    if consent
                    else None,
                    consent_source=rng.choice(["paper_form", "verbal", "import"])
                    if consent
                    else "",
                    opted_out=opted_out,
                    opted_out_at=timezone.now() - dt.timedelta(days=rng.randint(1, 20))
                    if opted_out
                    else None,
                    status="inactive" if rng.random() < 0.03 else "active",
                )
            )
        Subscriber.objects.bulk_create(subscribers, batch_size=1000)

        schedule_type = template.schedule_types.get(key="cylinder_delivery")
        cylinder_types = tenant.settings["industry"]["choice_options"].get(
            "cylinder_delivery.cylinder_type"
        ) or ["14.2 kg domestic"]
        schedules, events, attempts, responses = [], [], [], []
        for n, subscriber in enumerate(subscribers):
            if rng.random() > 0.75:
                continue
            schedule, more = self.booking(
                tenant, schedule_type, subscriber, n, today, rng, cylinder_types
            )
            schedules.append(schedule)
            events.extend(more["events"])
            attempts.extend(more["attempts"])
            responses.extend(more["responses"])
        Schedule.objects.bulk_create(schedules, batch_size=1000)
        ScheduleEvent.objects.bulk_create(events, batch_size=1000)
        Attempt.objects.bulk_create(attempts, batch_size=1000)
        Response.objects.bulk_create(responses, batch_size=1000)
        jobs, follow_ups = self.follow_through(tenant, schedules, today, rng)
        return {
            "areas": len(areas),
            "staff": len(staff),
            "subscribers": len(subscribers),
            "bookings": len(schedules),
            "messages": sum(a.channel == Attempt.Channel.WHATSAPP for a in attempts),
            "calls": sum(a.channel == Attempt.Channel.VOICE for a in attempts),
            "responses": len(responses),
            "delivery jobs": jobs,
            "follow-up items": follow_ups,
        }

    @staticmethod
    def mobile(rng):
        return f"+91{rng.choice('6789')}{rng.randint(0, 999_999_999):09d}"

    def booking(self, tenant, schedule_type, subscriber, n, today, rng, cylinder_types):
        """One booking with its reminder history, as the Phase 2 engine would leave it."""
        roll = rng.random()
        if roll < 0.10:
            scheduled = None  # booked, no delivery date yet
        elif roll < 0.55:
            scheduled = today - dt.timedelta(days=rng.randint(1, 6))
        elif roll < 0.85:
            scheduled = today + dt.timedelta(days=1)
        else:
            scheduled = today + dt.timedelta(days=rng.randint(2, 4))
        booked_on = (scheduled or today) - dt.timedelta(days=rng.randint(1, 4))
        booked_on = min(booked_on, today)
        schedule = Schedule(
            id=uuid.uuid4(),
            tenant=tenant,
            schedule_type=schedule_type,
            subscriber=subscriber,
            booking_reference=f"BK{26_000_000 + n}",
            booking_date=booked_on,
            scheduled_date=scheduled,
            custom_fields={
                "cylinder_type": rng.choices(
                    cylinder_types, weights=[80] + [5] * (len(cylinder_types) - 1)
                )[0],
                "quantity": 2 if rng.random() < 0.05 else 1,
            },
        )
        created_at = dt.datetime.combine(booked_on, dt.time(9, rng.randint(0, 59)), IST)
        events = [self.event(tenant, schedule, "created", created_at, {"source": "import"})]
        attempts, responses = [], []
        more = {"events": events, "attempts": attempts, "responses": responses}

        if rng.random() < 0.02:
            schedule.status, schedule.cancelled_at = Schedule.Status.CANCELLED, created_at
            schedule.cancel_reason = "Customer cancelled by phone"
            events.append(self.event(tenant, schedule, "cancelled", created_at, {}))
            return schedule, more
        reminder_day = scheduled - dt.timedelta(days=1) if scheduled else None
        if scheduled is None or reminder_day > today or not subscriber.can_be_messaged():
            if scheduled and not subscriber.can_be_messaged():
                schedule.outcome = Outcome.SKIPPED
                schedule.status = Schedule.Status.CLOSED
            return schedule, more

        outcome = rng.choices([o for o, _ in OUTCOME_WEIGHTS], [w for _, w in OUTCOME_WEIGHTS])[0]
        sent_at = dt.datetime.combine(reminder_day, dt.time(10, 0), IST) + dt.timedelta(
            seconds=rng.randint(0, 900)
        )
        message = Attempt(
            id=uuid.uuid4(),
            tenant=tenant,
            schedule=schedule,
            channel=Attempt.Channel.WHATSAPP,
            sent_at=sent_at,
            provider_message_id=f"wamid.demo.{uuid.uuid4().hex}",
            delivery_status=Attempt.DeliveryStatus.READ,
            cost_paise=WHATSAPP_COST_PAISE,
        )
        attempts.append(message)  # the timeline shows attempts and replies from their own tables
        replied_at = sent_at + dt.timedelta(minutes=rng.randint(2, 180))

        if outcome is None:
            schedule.status = Schedule.Status.AWAITING_REPLY
            message.delivery_status = Attempt.DeliveryStatus.DELIVERED
            return schedule, more
        if outcome == Outcome.FAILED:
            message.delivery_status = Attempt.DeliveryStatus.FAILED
            message.failure_reason = rng.choice(
                ["Number not on WhatsApp", "Message undeliverable (131026)"]
            )
            message.cost_paise = 0
        elif outcome == Outcome.NO_RESPONSE:
            # No reply after the wait: some got a follow-up voice call that wasn't answered.
            if rng.random() < 0.5:
                attempts.append(self.call(tenant, schedule, sent_at, rng, answered=False))
            # Decided when the wait (and any call) is over.
            replied_at = sent_at + dt.timedelta(hours=4, minutes=45)
        elif outcome in BUTTONS and rng.random() < 0.15:
            # Answered on a voice call after missing the WhatsApp message.
            message.delivery_status = Attempt.DeliveryStatus.DELIVERED
            call = self.call(tenant, schedule, sent_at, rng, answered=True)
            attempts.append(call)
            replied_at = call.sent_at + dt.timedelta(seconds=40)
            responses.append(
                Response(
                    tenant=tenant,
                    attempt=call,
                    received_at=replied_at,
                    transcript=CALL_TRANSCRIPTS[outcome],
                    recording_key=f"recordings/demo/{call.id}.mp3",
                    outcome=outcome,
                    confidence=round(rng.uniform(0.82, 0.99), 3),
                )
            )
        elif outcome in BUTTONS:
            payload, label = BUTTONS[outcome]
            responses.append(
                Response(
                    tenant=tenant,
                    attempt=message,
                    received_at=replied_at,
                    raw_text=label,
                    button_payload=payload,
                    outcome=outcome,
                    confidence=1,
                )
            )
        elif outcome == Outcome.NEEDS_REVIEW:
            responses.append(
                Response(
                    tenant=tenant,
                    attempt=message,
                    received_at=replied_at,
                    raw_text=rng.choice(NEEDS_REVIEW_TEXT),
                    outcome=outcome,
                    confidence=round(rng.uniform(0.3, 0.6), 3),
                )
            )
        elif outcome == Outcome.OPTED_OUT:
            responses.append(
                Response(
                    tenant=tenant,
                    attempt=message,
                    received_at=replied_at,
                    raw_text="STOP",
                    outcome=outcome,
                    confidence=1,
                )
            )

        # Half the "change date" answers pick a day; the rest need a call (follow-up queue).
        if outcome == Outcome.RESCHEDULE and rng.random() < 0.5:
            schedule.new_date_requested = scheduled + dt.timedelta(days=rng.randint(1, 4))
        schedule.outcome = outcome
        schedule.status = Schedule.Status.CLOSED
        schedule.answered_at = replied_at
        events.append(self.event(tenant, schedule, "outcome_set", replied_at, {"outcome": outcome}))
        return schedule, more

    def follow_through(self, tenant, schedules, today, rng):
        """
        Jobs and follow-up items as Workflows 3.1 and 3.2 leave them (same rules as
        apps.schedules.services): confirmed -> job for the area's default staff; a picked new day
        -> booking moved; any other outcome -> follow-up item, older ones already worked.
        """
        jobs, items, events, moved = [], [], [], []
        for schedule in schedules:
            outcome = schedule.outcome
            answered = getattr(schedule, "answered_at", None) or timezone.now()
            if outcome == Outcome.CONFIRMED and schedule.scheduled_date:
                job = plan_job(schedule)
                if schedule.scheduled_date < today:
                    job.status = Job.Status.DONE
                jobs.append(job)
                events.append(
                    self.event(
                        tenant,
                        schedule,
                        "job_created",
                        answered,
                        {"staff": job.assigned_staff.name if job.assigned_staff else None},
                    )
                )
            elif (
                outcome == Outcome.RESCHEDULE
                and schedule.new_date_requested
                and schedule.new_date_requested >= today
            ):
                previous = schedule.scheduled_date
                schedule.scheduled_date, schedule.new_date_requested = (
                    schedule.new_date_requested,
                    None,
                )
                schedule.status, schedule.outcome = Schedule.Status.BOOKED, ""
                moved.append(schedule)
                events.append(
                    self.event(
                        tenant,
                        schedule,
                        "date_changed",
                        answered,
                        {
                            "previous_date": previous.isoformat(),
                            "scheduled_date": schedule.scheduled_date.isoformat(),
                            "reason": "customer_reschedule",
                        },
                    )
                )
            elif outcome in FOLLOW_UP_OUTCOMES:
                item = FollowUpItem(
                    tenant=tenant, schedule=schedule, outcome=outcome, opened_at=answered
                )
                age = timezone.now() - answered
                if age > dt.timedelta(days=2):
                    item.status, item.resolution = FollowUpItem.Status.RESOLVED, "resolved"
                    item.resolved_at = answered + dt.timedelta(hours=rng.randint(2, 30))
                elif rng.random() < 0.3:
                    item.status, item.contacted_at = (
                        FollowUpItem.Status.CONTACTED,
                        answered + dt.timedelta(hours=1),
                    )
                    item.notes = [
                        {
                            "at": item.contacted_at.isoformat(),
                            "by": "staff@demo-gas.test",
                            "text": "Called; customer will confirm by evening.",
                        }
                    ]
                items.append(item)
                events.append(
                    self.event(tenant, schedule, "follow_up_opened", answered, {"outcome": outcome})
                )
        Schedule.objects.bulk_update(
            moved, ["scheduled_date", "new_date_requested", "status", "outcome"], batch_size=1000
        )
        Job.objects.bulk_create(jobs, batch_size=1000)
        FollowUpItem.objects.bulk_create(items, batch_size=1000)
        ScheduleEvent.objects.bulk_create(events, batch_size=1000)
        return len(jobs), len(items)

    @staticmethod
    def call(tenant, schedule, after, rng, answered):
        return Attempt(
            id=uuid.uuid4(),
            tenant=tenant,
            schedule=schedule,
            channel=Attempt.Channel.VOICE,
            sent_at=after + dt.timedelta(hours=4, minutes=rng.randint(0, 30)),
            provider_message_id=f"call.demo.{uuid.uuid4().hex}",
            delivery_status=Attempt.DeliveryStatus.ANSWERED
            if answered
            else Attempt.DeliveryStatus.FAILED,
            failure_reason="" if answered else "No answer",
            cost_paise=VOICE_COST_PAISE if answered else 0,
        )

    @staticmethod
    def event(tenant, schedule, kind, at, data):
        return ScheduleEvent(tenant=tenant, schedule=schedule, kind=kind, occurred_at=at, data=data)

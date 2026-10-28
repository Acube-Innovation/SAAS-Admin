"""
Portal Users (spec 2.10, owner only), Reminder settings (2.10, owner and manager) and the agency's
details (read-only; platform staff edit them in the admin console).
"""

import datetime as dt
import re

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.translation import gettext as _
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, TenantRole, User
from apps.accounts.permissions import tenant_roles
from apps.accounts.services import create_invite, resend_invite
from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.constants import Language
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.industry.services import tenant_config
from apps.messaging_templates.models import WhatsAppTemplate

from . import services

OWNER = tenant_roles("owner")
OWNER_MANAGER = tenant_roles("owner", "manager")
ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
INVITABLE_ROLES = TenantRole.choices  # owner, manager, office_staff


# --- users ----------------------------------------------------------------------------------


class TeamMemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    last_login = serializers.DateTimeField(
        source="user.last_login", read_only=True, allow_null=True
    )
    otp_enabled = serializers.BooleanField(read_only=True, default=False)
    is_you = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = [
            "id",
            "email",
            "full_name",
            "role",
            "status",
            "last_login",
            "otp_enabled",
            "is_you",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_you(self, membership) -> bool:
        return membership.user_id == self.context["request"].user.pk


class InviteMemberSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    full_name = serializers.CharField(min_length=2, max_length=100, trim_whitespace=True)
    role = serializers.ChoiceField(choices=INVITABLE_ROLES)

    def validate_email(self, value):
        return value.strip().lower()


class ChangeRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=INVITABLE_ROLES)


def _active_owner_count(tenant_id):
    return Membership.objects.filter(
        tenant_id=tenant_id, role=TenantRole.OWNER, status=Membership.Status.ACTIVE
    ).count()


def _guard_last_owner(membership):
    """The last active owner can't be demoted or deactivated (spec 2.10)."""
    if (
        membership.role == TenantRole.OWNER
        and membership.status == Membership.Status.ACTIVE
        and _active_owner_count(membership.tenant_id) <= 1
    ):
        raise ApiError(
            "last_owner",
            _("The agency needs at least one active owner. Make someone else owner first."),
            409,
        )


@extend_schema(tags=["portal: users and settings"])
class TeamViewSet(PortalViewMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """The agency's portal users: invite, change role, deactivate, resend invite (owner only)."""

    serializer_class = TeamMemberSerializer
    permission_classes = [OWNER]
    pagination_class = None

    def get_queryset(self):
        return (
            self.scoped(Membership.objects.select_related("user"))
            .annotate(
                otp_enabled=Exists(TOTPDevice.objects.filter(user=OuterRef("user"), confirmed=True))
            )
            .order_by("status", "created_at")
        )

    def _member(self, pk):
        membership = self.get_queryset().filter(pk=pk).first()
        if membership is None:
            raise ApiError("not_found", _("Not found."), 404)
        return membership

    def _respond(self, membership, status=200):
        return Response(self.get_serializer(self._member(membership.pk)).data, status=status)

    @extend_schema(request=InviteMemberSerializer, responses={201: TeamMemberSerializer})
    def create(self, request):
        data = InviteMemberSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email, role = data.validated_data["email"], data.validated_data["role"]
        with transaction.atomic():
            user = User.objects.filter(email__iexact=email).first()
            if user is not None and user.is_platform_staff:
                raise ApiError("staff_account", _("This email belongs to platform staff."), 400)
            if user and Membership.objects.filter(tenant_id=request.tenant_id, user=user).exists():
                raise ApiError("already_member", _("This person is already on your team."), 409)
            if user is None:
                user = User.objects.create_user(email, full_name=data.validated_data["full_name"])
                write_audit(Action.CREATE, user, after=snapshot(user))
            membership = Membership.objects.create(
                tenant_id=request.tenant_id, user=user, role=role, status=Membership.Status.INVITED
            )
            write_audit(Action.CREATE, membership, after=snapshot(membership))
            create_invite(membership, created_by=request.user)
        return self._respond(membership, status=201)

    @extend_schema(request=ChangeRoleSerializer, responses=TeamMemberSerializer)
    @action(detail=True, methods=["post"])
    def role(self, request, pk=None):
        data = ChangeRoleSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            membership = Membership.objects.select_for_update().get(pk=self._member(pk).pk)
            new_role = data.validated_data["role"]
            if new_role != membership.role:
                if membership.role == TenantRole.OWNER:
                    _guard_last_owner(membership)
                before = snapshot(membership)
                membership.role = new_role
                membership.save(update_fields=["role", "updated_at"])
                write_audit(Action.UPDATE, membership, before=before, after=snapshot(membership))
        return self._respond(membership)

    @extend_schema(request=None, responses=TeamMemberSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        with transaction.atomic():
            membership = Membership.objects.select_for_update().get(pk=self._member(pk).pk)
            if membership.status != Membership.Status.DEACTIVATED:
                _guard_last_owner(membership)
                before = snapshot(membership)
                membership.status = Membership.Status.DEACTIVATED
                membership.save(update_fields=["status", "updated_at"])
                write_audit(
                    Action.STATUS_CHANGE, membership, before=before, after=snapshot(membership)
                )
        return self._respond(membership)

    @extend_schema(request=None, responses=TeamMemberSerializer)
    @action(detail=True, methods=["post"], url_path="resend-invite")
    def resend(self, request, pk=None):
        membership = self._member(pk)
        resend_invite(membership, created_by=request.user)
        return self._respond(membership)


# --- reminder settings ----------------------------------------------------------------------

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_HOLIDAYS = 60


def _minutes(value):
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def in_quiet_hours(time, quiet_start, quiet_end):
    """Quiet hours may wrap midnight (21:00-08:00)."""
    t, start, end = _minutes(time), _minutes(quiet_start), _minutes(quiet_end)
    if start == end:
        return False
    return start <= t < end if start < end else t >= start or t < end


class HHMMField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data).strip()
        if not TIME_RE.match(value):
            raise serializers.ValidationError(_("Use HH:MM (24-hour)."))
        return value


class LinkedTemplateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    language = serializers.CharField()
    live_version = serializers.IntegerField(allow_null=True)
    latest_status = serializers.CharField()


class ReminderSettingsSerializer(serializers.Serializer):
    schedule_type = serializers.CharField(read_only=True)
    send_time = HHMMField()
    days_before = serializers.IntegerField(min_value=0, max_value=7)
    wait_minutes = serializers.IntegerField(min_value=30, max_value=1440)
    quiet_start = HHMMField()
    quiet_end = HHMMField()
    default_language = serializers.ChoiceField(choices=Language.choices)
    delivery_weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6), min_length=1, max_length=7
    )
    holiday_dates = serializers.ListField(
        child=serializers.DateField(), max_length=MAX_HOLIDAYS, required=False
    )
    linked_templates = LinkedTemplateSerializer(many=True, read_only=True)
    saved_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def validate_delivery_weekdays(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError(_("Each day once."))
        return sorted(value)

    def validate_holiday_dates(self, value):
        today = timezone.localdate()
        if any(day < today for day in value):
            raise serializers.ValidationError(_("Holidays must be today or later."))
        return sorted(set(value))

    def validate(self, attrs):
        if in_quiet_hours(attrs["send_time"], attrs["quiet_start"], attrs["quiet_end"]):
            raise serializers.ValidationError(
                {"send_time": [_("The send time is inside quiet hours.")]}
            )
        return attrs


def reminder_settings(tenant):
    config = tenant_config(tenant)
    schedule_type = (
        config["schedule_types"][0]
        if config["schedule_types"]
        else {"key": "", "reminder_rule": {}}
    )
    rule = schedule_type["reminder_rule"]
    templates = []
    for template in (
        WhatsAppTemplate.objects.filter(tenant=tenant).prefetch_related("versions").order_by("name")
    ):
        versions = sorted(template.versions.all(), key=lambda v: v.version)
        live = next((v for v in versions if v.is_live), None)
        templates.append(
            {
                "id": template.pk,
                "name": template.name,
                "language": template.language,
                "live_version": live.version if live else None,
                "latest_status": versions[-1].status if versions else "",
            }
        )
    onboarding = (tenant.settings or {}).get("onboarding", {})
    return {
        "schedule_type": schedule_type["key"],
        "send_time": rule.get("send_time", "10:00"),
        "days_before": rule.get("days_before", 1),
        "wait_minutes": rule.get("wait_minutes", 240),
        "quiet_start": rule.get("quiet_start", "21:00"),
        "quiet_end": rule.get("quiet_end", "08:00"),
        "default_language": tenant.default_language,
        "delivery_weekdays": rule.get("delivery_weekdays", [0, 1, 2, 3, 4, 5]),
        "holiday_dates": [
            dt.date.fromisoformat(d)
            for d in rule.get("holiday_dates", [])
            if d >= timezone.localdate().isoformat()
        ],
        "linked_templates": templates,
        "saved_at": onboarding.get("done", {}).get("reminder_settings"),
    }


def save_reminder_settings(tenant, data):
    before = reminder_settings(tenant)
    key = before["schedule_type"]
    settings = dict(tenant.settings or {})
    industry = dict(settings.get("industry", {}))
    types = dict(industry.get("schedule_types", {}))
    entry = dict(types.get(key, {}))
    rule = dict(
        entry.get("reminder_rule") or tenant_config(tenant)["schedule_types"][0]["reminder_rule"]
    )
    rule.update(
        send_time=data["send_time"],
        days_before=data["days_before"],
        wait_minutes=data["wait_minutes"],
        quiet_start=data["quiet_start"],
        quiet_end=data["quiet_end"],
        delivery_weekdays=data["delivery_weekdays"],
        holiday_dates=[d.isoformat() for d in data.get("holiday_dates", [])],
    )
    entry["reminder_rule"] = rule
    types[key] = entry
    industry["schedule_types"] = types
    settings["industry"] = industry
    tenant.settings = settings
    tenant.default_language = data["default_language"]
    tenant.save(update_fields=["settings", "default_language", "updated_at"])
    services.mark_checklist(tenant, "reminder_settings", "done")  # Workflow 3.6: saving ticks it
    after = reminder_settings(tenant)
    strip = lambda d: {k: v for k, v in d.items() if k not in ("linked_templates", "saved_at")}  # noqa: E731
    write_audit(
        Action.UPDATE,
        tenant,
        before={"reminder_settings": strip(before)},
        after={"reminder_settings": strip(after)},
        tenant=tenant,
    )
    return after


@extend_schema(tags=["portal: users and settings"])
class ReminderSettingsView(APIView):
    """The agency's reminder rule. Changes apply to reminders not yet sent (spec 2.10)."""

    permission_classes = [OWNER_MANAGER]

    @extend_schema(responses=ReminderSettingsSerializer)
    def get(self, request):
        tenant = Tenant.objects.get(pk=request.tenant_id)
        return Response(ReminderSettingsSerializer(reminder_settings(tenant)).data)

    @extend_schema(request=ReminderSettingsSerializer, responses=ReminderSettingsSerializer)
    def put(self, request):
        data = ReminderSettingsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=request.tenant_id)
            result = save_reminder_settings(tenant, data.validated_data)
        return Response(ReminderSettingsSerializer(result).data)


# --- agency details -------------------------------------------------------------------------


class AgencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "legal_name",
            "gstin",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "pin_code",
            "contact_name",
            "contact_phone",
            "contact_email",
            "default_language",
            "daily_send_cap",
            "status",
        ]
        read_only_fields = fields


@extend_schema(tags=["portal: users and settings"])
class AgencyView(APIView):
    """The agency's details as the platform holds them (confirmed by the owner at first login)."""

    permission_classes = [ALL_ROLES]

    @extend_schema(responses=AgencySerializer)
    def get(self, request):
        return Response(AgencySerializer(Tenant.objects.get(pk=request.tenant_id)).data)

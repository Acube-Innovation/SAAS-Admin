"""Admin console billing API (admin spec 2.11, 3.8). Platform staff read; super admins change."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsPlatformStaff, IsSuperAdmin
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context
from apps.subscribers.models import Subscriber

from . import plans, services
from .admin_serializers import (
    ChangePlanSerializer,
    OverrideCreateSerializer,
    OverrideEndSerializer,
    OverrideSerializer,
    PlanAdminSerializer,
    PlanUpdateSerializer,
    PriceCreateSerializer,
    PriceEntrySerializer,
    PriceListAdminSerializer,
    TenantBillingSerializer,
)
from .models import Channel, Invoice, Plan, PriceList, PriceOverride, WalletTransaction
from .payment_services import live_mandate, subscription_for

TAGS = ["admin: billing"]


class StaffReadSuperAdminWrite:
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsPlatformStaff()]
        return [IsSuperAdmin()]


@extend_schema(tags=TAGS)
class PlanAdminViewSet(
    StaffReadSuperAdminWrite,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Plans (spec 2.11): list, add, edit. Codes never change; plans are retired, not deleted."""

    queryset = Plan.objects.all().order_by("monthly_fee_paise", "code")
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        return PlanUpdateSerializer if self.action == "partial_update" else PlanAdminSerializer

    def perform_create(self, serializer):
        serializer.instance = plans.create_plan(dict(serializer.validated_data), self.request.user)

    def perform_update(self, serializer):
        serializer.instance = plans.update_plan(
            serializer.instance, dict(serializer.validated_data), self.request.user
        )


class PriceListAdminView(StaffReadSuperAdminWrite, APIView):
    """The platform price list: current prices and every entry; add a price from a date."""

    @extend_schema(tags=TAGS, responses=PriceListAdminSerializer)
    def get(self, request):
        current = plans.current_prices()
        return Response(
            PriceListAdminSerializer(
                {
                    "current": [{"channel": c, "price_paise": p} for c, p in current.items()],
                    "entries": PriceList.objects.order_by("channel", "-effective_from"),
                }
            ).data
        )

    @extend_schema(tags=TAGS, request=PriceCreateSerializer, responses={201: PriceEntrySerializer})
    def post(self, request):
        data = PriceCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        entry = plans.add_price(**data.validated_data, actor=request.user)
        return Response(PriceEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class PriceEntryAdminView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk):
        plans.delete_price(get_object_or_404(PriceList, pk=pk), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _tenant(pk):
    return get_object_or_404(Tenant, pk=pk)


def billing_view(tenant):
    today = timezone.localdate()
    subscription = subscription_for(tenant)
    wallet = services.wallet_for(tenant)
    level = services.low_balance_level(tenant)
    with tenant_context(tenant):
        overridden = {
            o.channel
            for o in PriceOverride.objects.filter(tenant=tenant, starts_on__lte=today)
            if o.ends_on is None or o.ends_on >= today
        }
        data = {
            "subscription": {
                "plan": subscription.plan if subscription else None,
                "pending_plan": subscription.pending_plan if subscription else None,
                "status": subscription.status if subscription else None,
                "current_period_start": subscription.current_period_start if subscription else None,
                "next_billing_date": subscription.next_billing_date if subscription else None,
            },
            "wallet": {
                "balance_paise": wallet.balance_paise,
                "low_balance_level_paise": level,
                "is_low": wallet.balance_paise < level,
                "can_send": services.can_send(tenant),
                "paused_at": wallet.paused_at,
            },
            "mandate": live_mandate(tenant),
            "prices": [
                {
                    "channel": c,
                    "price_paise": services.price_for(tenant, c, today),
                    "source": "override" if c in overridden else "price_list",
                }
                for c in Channel.values
            ],
            "active_subscribers": Subscriber.objects.filter(tenant=tenant, status="active").count(),
            "recent_transactions": list(
                WalletTransaction.objects.filter(tenant=tenant)
                .select_related("invoice")
                .order_by("-created_at")[:10]
            ),
            "invoices": list(Invoice.objects.filter(tenant=tenant).order_by("-issued_at")[:10]),
        }
        return TenantBillingSerializer(data).data


class TenantBillingView(APIView):
    """Client detail Billing tab: plan and pending change, prices, read-only wallet."""

    permission_classes = [IsPlatformStaff]

    @extend_schema(tags=TAGS, responses=TenantBillingSerializer)
    def get(self, request, pk):
        return Response(billing_view(_tenant(pk)))


class TenantPlanChangeView(APIView):
    """Schedule a plan change for the next billing date, or cancel the pending one."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(tags=TAGS, request=ChangePlanSerializer, responses=TenantBillingSerializer)
    def post(self, request, pk):
        tenant = _tenant(pk)
        data = ChangePlanSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        plan = get_object_or_404(Plan, code=data.validated_data["plan_code"])
        plans.schedule_plan_change(tenant, plan, request.user)
        return Response(billing_view(tenant))

    @extend_schema(tags=TAGS, responses=TenantBillingSerializer)
    def delete(self, request, pk):
        tenant = _tenant(pk)
        plans.cancel_plan_change(tenant, request.user)
        return Response(billing_view(tenant))


def _override_context():
    return {"today": timezone.localdate()}


class TenantPriceOverridesView(StaffReadSuperAdminWrite, APIView):
    @extend_schema(tags=TAGS, responses=OverrideSerializer(many=True))
    def get(self, request, pk):
        tenant = _tenant(pk)
        with tenant_context(tenant):
            overrides = list(PriceOverride.objects.filter(tenant=tenant))
        return Response(OverrideSerializer(overrides, many=True, context=_override_context()).data)

    @extend_schema(tags=TAGS, request=OverrideCreateSerializer, responses={201: OverrideSerializer})
    def post(self, request, pk):
        data = OverrideCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        override = plans.add_override(_tenant(pk), data.validated_data, request.user)
        return Response(
            OverrideSerializer(override, context=_override_context()).data,
            status=status.HTTP_201_CREATED,
        )


class TenantPriceOverrideView(APIView):
    permission_classes = [IsSuperAdmin]

    def _override(self, pk, override_id):
        tenant = _tenant(pk)
        with tenant_context(tenant):
            return get_object_or_404(PriceOverride, pk=override_id, tenant=tenant)

    @extend_schema(tags=TAGS, request=OverrideEndSerializer, responses=OverrideSerializer)
    def patch(self, request, pk, override_id):
        data = OverrideEndSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        override = plans.end_override(
            self._override(pk, override_id), data.validated_data["ends_on"], request.user
        )
        return Response(OverrideSerializer(override, context=_override_context()).data)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk, override_id):
        plans.delete_override(self._override(pk, override_id), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

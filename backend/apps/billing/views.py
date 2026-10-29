"""Wallet and billing (portal spec 2.9): wallet, invoices, top-up, auto top-up and plan."""

from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.change_requests.serializers import ChangeRequestDetailSerializer
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.core.storage import signed_url
from apps.schedules.models import Schedule
from apps.subscribers.models import Subscriber

from . import payment_services, services
from .models import Channel, Invoice, PaymentOrder, Plan, WalletTransaction
from .payments import StubProvider
from .serializers import (
    AutoTopUpSerializer,
    InvoiceLinkSerializer,
    InvoiceSerializer,
    MandateAmountSerializer,
    MandateCreateSerializer,
    MandateSerializer,
    MandateStartedSerializer,
    PaymentOrderSerializer,
    PaymentSimulateSerializer,
    PlanChangeRequestSerializer,
    PlanViewSerializer,
    TopUpCreateSerializer,
    TopUpStartedSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)

# Wallet and billing: owner; manager views (spec 1.1). Support may view in a support session.
BILLING = tenant_roles("owner", "manager")
TAGS = ["portal: wallet and billing"]


class BillingPagination(PageNumberPagination):
    page_size = 50


class WalletView(APIView):
    permission_classes = [BILLING]

    @extend_schema(tags=TAGS, responses=WalletSerializer)
    def get(self, request):
        tenant = Tenant.objects.get(pk=request.tenant_id)
        wallet = services.wallet_for(tenant)
        level = services.low_balance_level(tenant)
        data = {
            "balance_paise": wallet.balance_paise,
            "low_balance_level_paise": level,
            "is_low": wallet.balance_paise < level,
            "can_send": services.can_send(tenant),
            "paused_at": wallet.paused_at,
            "held_reminders": Schedule.objects.filter(
                tenant=tenant, reminder_held_at__isnull=False
            ).count(),
            "prices": [
                {"channel": c, "price_paise": services.price_for(tenant, c)} for c in Channel.values
            ],
        }
        return Response(WalletSerializer(data).data)


@extend_schema(tags=TAGS)
class WalletTransactionViewSet(PortalViewMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = WalletTransactionSerializer
    permission_classes = [BILLING]
    pagination_class = BillingPagination

    def get_queryset(self):
        queryset = self.scoped(WalletTransaction.objects.select_related("invoice"))
        if (kind := self.request.query_params.get("kind")) in WalletTransaction.Kind.values:
            queryset = queryset.filter(kind=kind)
        return queryset.order_by("-created_at")

    @extend_schema(parameters=[OpenApiParameter("kind", str, enum=["credit", "debit"])])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=TAGS)
class InvoiceViewSet(
    PortalViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = InvoiceSerializer
    permission_classes = [BILLING]
    pagination_class = BillingPagination

    def get_queryset(self):
        return self.scoped(Invoice.objects.all()).order_by("-issued_at")

    @extend_schema(responses=InvoiceLinkSerializer)
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """A download link for the invoice PDF, valid for 5 minutes."""
        invoice = self.get_object()
        if not invoice.pdf:
            raise ApiError("no_pdf", "This invoice has no PDF.", 404)
        return Response({"url": signed_url(invoice.pdf.name), "expires_in": 300})


# --- payments (Step 21) -----------------------------------------------------------------------

OWNER = tenant_roles("owner")


def _tenant(request):
    return Tenant.objects.get(pk=request.tenant_id)


class TopUpView(APIView):
    """Start a manual top-up (owner). The wallet is credited only by the provider's webhook."""

    permission_classes = [OWNER]

    @extend_schema(
        tags=TAGS, request=TopUpCreateSerializer, responses={201: TopUpStartedSerializer}
    )
    def post(self, request):
        data = TopUpCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        order, checkout = payment_services.start_top_up(
            _tenant(request), data.validated_data["credit_paise"], request.user
        )
        return Response(
            {"order": PaymentOrderSerializer(order).data, "checkout": checkout.__dict__}, status=201
        )


class TopUpDetailView(PortalViewMixin, APIView):
    """Follow a top-up after checkout (the screen polls until the webhook marks it paid)."""

    permission_classes = [BILLING]

    @extend_schema(tags=TAGS, responses=PaymentOrderSerializer)
    def get(self, request, pk):
        order = self.scoped(PaymentOrder.objects.all()).filter(pk=pk).first()
        if order is None:
            raise ApiError("not_found", "Not found.", 404)
        return Response(PaymentOrderSerializer(order).data)


class AutoTopUpView(APIView):
    """Auto top-up: view (owner, manager), set up (owner)."""

    def get_permissions(self):
        return [OWNER()] if self.request.method == "POST" else [BILLING()]

    def _state(self, tenant):
        return {
            "mandate": payment_services.live_mandate(tenant),
            "min_credit_paise": settings.AUTO_TOP_UP_MIN_PAISE,
            "max_credit_paise": settings.AUTO_TOP_UP_MAX_PAISE,
            "low_balance_level_paise": services.low_balance_level(tenant),
        }

    @extend_schema(tags=TAGS, responses=AutoTopUpSerializer)
    def get(self, request):
        return Response(AutoTopUpSerializer(self._state(_tenant(request))).data)

    @extend_schema(
        tags=TAGS, request=MandateCreateSerializer, responses={201: MandateStartedSerializer}
    )
    def post(self, request):
        data = MandateCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        mandate, checkout = payment_services.start_mandate(
            _tenant(request),
            data.validated_data["credit_paise"],
            data.validated_data["method"],
            request.user,
        )
        return Response(
            {"mandate": MandateSerializer(mandate).data, "checkout": checkout.__dict__}, status=201
        )


class AutoTopUpActionView(APIView):
    """Change the amount, pause, resume or cancel auto top-up (owner)."""

    permission_classes = [OWNER]
    ACTIONS = {
        "pause": payment_services.pause_mandate,
        "resume": payment_services.resume_mandate,
        "cancel": payment_services.cancel_mandate,
    }

    def _mandate(self, request):
        mandate = payment_services.live_mandate(_tenant(request))
        if mandate is None:
            raise ApiError("no_mandate", "Auto top-up isn't set up.", 404)
        return mandate

    @extend_schema(
        tags=TAGS,
        operation_id="portal_auto_top_up_action",
        request=MandateAmountSerializer,
        responses=MandateSerializer,
        parameters=[
            OpenApiParameter(
                "action", str, OpenApiParameter.PATH, enum=["amount", "pause", "resume", "cancel"]
            )
        ],
    )
    def post(self, request, action):
        mandate = self._mandate(request)
        if action == "amount":
            data = MandateAmountSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            mandate = payment_services.set_mandate_amount(
                mandate, data.validated_data["credit_paise"], request.user
            )
        elif action in self.ACTIONS:
            mandate = self.ACTIONS[action](mandate, request.user)
        else:
            raise ApiError("not_found", "Unknown action.", 404)
        return Response(MandateSerializer(mandate).data)


class PlanView(APIView):
    """The agency's plan, view-only (spec 2.9), with the plans it could move to."""

    permission_classes = [BILLING]

    @extend_schema(tags=TAGS, responses=PlanViewSerializer)
    def get(self, request):
        tenant = _tenant(request)
        subscription = payment_services.subscription_for(tenant)
        active = Subscriber.objects.filter(tenant=tenant, status="active").count()
        return Response(
            PlanViewSerializer(
                {
                    "plan": subscription.plan if subscription else None,
                    "pending_plan": subscription.pending_plan if subscription else None,
                    "status": subscription.status if subscription else None,
                    "current_period_start": subscription.current_period_start
                    if subscription
                    else None,
                    "next_billing_date": subscription.next_billing_date if subscription else None,
                    "active_subscribers": active,
                    "plans": Plan.objects.filter(is_active=True),
                }
            ).data
        )


class PlanChangeRequestView(APIView):
    """Request a plan change (owner): raises a change request for the platform team."""

    permission_classes = [OWNER]

    @extend_schema(
        tags=TAGS,
        request=PlanChangeRequestSerializer,
        responses={201: ChangeRequestDetailSerializer},
    )
    def post(self, request):
        from apps.change_requests.models import ChangeRequest
        from apps.change_requests.services import raise_request

        data = PlanChangeRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        plan = Plan.objects.filter(code=data.validated_data["plan_code"], is_active=True).first()
        if plan is None:
            raise ApiError("validation_error", "Pick one of the plans shown.", 400)
        tenant = _tenant(request)
        subscription = payment_services.subscription_for(tenant)
        if subscription and subscription.plan_id == plan.pk:
            raise ApiError("validation_error", "That is your current plan.", 400)
        change_request = raise_request(
            tenant,
            request.user,
            {
                "request_type": ChangeRequest.RequestType.PLAN_CHANGE,
                "subject": f"Plan change to {plan.name}"[:150],
                "description": data.validated_data["note"],
            },
        )
        detail = (
            ChangeRequest.objects.select_related("requested_by")
            .prefetch_related("comments")
            .get(pk=change_request.pk)
        )
        return Response(ChangeRequestDetailSerializer(detail).data, status=201)


class PaymentWebhookView(APIView):
    """Signed provider webhooks: the only way money reaches a wallet. Public; signature-checked."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["payments: webhooks"], request=None, responses={200: None})
    def post(self, request, provider):
        outcome = payment_services.handle_webhook(provider, request.body, request.headers)
        return Response({"status": outcome})


class SimulatePaymentView(APIView):
    """
    Local development only (DEBUG + the stub provider): deliver the signed webhook a provider would
    send, optionally more than once. Answers 404 everywhere else.
    """

    permission_classes = [OWNER]

    @extend_schema(tags=TAGS, request=PaymentSimulateSerializer, responses={200: None})
    def post(self, request):
        if not (settings.DEBUG or settings.TESTING) or not StubProvider().configured():
            raise ApiError("not_found", "Not found.", 404)
        data = PaymentSimulateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        event = payment_services.simulated_event(_tenant(request), **data.validated_data)
        outcomes = []
        for _ in range(data.validated_data["times"]):
            body, headers = StubProvider.signed(event)
            outcomes.append(payment_services.handle_webhook("stub", body, headers))
        return Response({"outcomes": outcomes})

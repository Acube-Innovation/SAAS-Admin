"""Wallet and billing, read-only (portal spec 2.9). Top-up and auto top-up arrive with Step 21."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.core.storage import signed_url
from apps.schedules.models import Schedule

from . import services
from .models import Channel, Invoice, WalletTransaction
from .serializers import (
    InvoiceLinkSerializer,
    InvoiceSerializer,
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

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsPlatformStaff, IsSuperAdmin

from . import services
from .serializers import (
    IndustryTemplateSerializer,
    IndustryTemplateVersionSerializer,
    TemplateInputSerializer,
)


@extend_schema(tags=["admin: industry templates"])
class IndustryTemplateViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Industry template editor (spec 2.6). PUT saves the whole definition as a new version."""

    serializer_class = IndustryTemplateSerializer
    pagination_class = None
    http_method_names = ["get", "post", "put", "head", "options"]

    def get_permissions(self):
        if self.action in ("create", "update"):
            return [IsSuperAdmin()]
        return [IsPlatformStaff()]

    def get_queryset(self):
        return services.templates_queryset()

    def _respond(self, template, status_code=status.HTTP_200_OK):
        return Response(
            IndustryTemplateSerializer(self.get_queryset().get(pk=template.pk)).data,
            status=status_code,
        )

    @extend_schema(request=TemplateInputSerializer, responses={201: IndustryTemplateSerializer})
    def create(self, request):
        serializer = TemplateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = services.create_template(serializer.validated_data, request.user)
        return self._respond(template, status.HTTP_201_CREATED)

    @extend_schema(request=TemplateInputSerializer, responses=IndustryTemplateSerializer)
    def update(self, request, pk=None):
        template = self.get_object()
        serializer = TemplateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = services.save_template(template, serializer.validated_data, request.user)
        return self._respond(template)

    @extend_schema(responses=IndustryTemplateVersionSerializer(many=True))
    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        template = self.get_object()
        versions = template.versions.select_related("created_by").order_by("-version")
        return Response(IndustryTemplateVersionSerializer(versions, many=True).data)

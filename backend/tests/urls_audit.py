"""Test-only URLs: an audited viewset over the test Note model."""

from rest_framework import serializers, viewsets
from rest_framework.routers import SimpleRouter

from apps.accounts.permissions import tenant_roles
from apps.audit.mixins import AuditedModelMixin
from config.urls import urlpatterns as real_urlpatterns
from tests.tenancy_app.models import Note


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "body", "status"]


class NoteViewSet(AuditedModelMixin, viewsets.ModelViewSet):
    serializer_class = NoteSerializer
    permission_classes = [tenant_roles("owner", "manager", "office_staff")]
    queryset = Note.objects.all()


router = SimpleRouter(trailing_slash=False)
router.register("t/notes", NoteViewSet, basename="note")
urlpatterns = [*real_urlpatterns, *router.urls]

from rest_framework import serializers

from apps.accounts.models import User
from apps.messaging_templates.models import WhatsAppTemplateVersion

from .models import ChangeRequest, ChangeRequestComment


def display_name(user) -> str:
    return user.full_name or user.email


class LinkedVersionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    template_name = serializers.CharField(source="template.name")
    language = serializers.CharField(source="template.language")
    version = serializers.IntegerField()
    status = serializers.CharField()
    is_live = serializers.BooleanField()


class CommentSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequestComment
        fields = ["id", "author_name", "from_staff", "body", "created_at"]
        read_only_fields = fields

    def get_author_name(self, obj) -> str:
        return display_name(obj.author)


class StaffCommentSerializer(CommentSerializer):
    author_email = serializers.EmailField(source="author.email")

    class Meta(CommentSerializer.Meta):
        fields = [*CommentSerializer.Meta.fields, "author_email"]


class ChangeRequestSerializer(serializers.ModelSerializer):
    """The client's view (portal)."""

    requested_by_name = serializers.SerializerMethodField()
    linked_version = LinkedVersionSerializer(allow_null=True, read_only=True)
    comment_count = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequest
        fields = [
            "id",
            "request_type",
            "subject",
            "description",
            "status",
            "requested_by_name",
            "linked_version",
            "decline_reason",
            "comment_count",
            "created_at",
            "updated_at",
            "started_at",
            "closed_at",
        ]
        read_only_fields = fields

    def get_requested_by_name(self, obj) -> str:
        return display_name(obj.requested_by)

    def get_comment_count(self, obj) -> int:
        return len(obj.comments.all())


class ChangeRequestDetailSerializer(ChangeRequestSerializer):
    comments = CommentSerializer(many=True, read_only=True)

    class Meta(ChangeRequestSerializer.Meta):
        fields = [*ChangeRequestSerializer.Meta.fields, "comments"]


class ChangeRequestCreateSerializer(serializers.Serializer):
    request_type = serializers.ChoiceField(choices=ChangeRequest.RequestType.choices)
    subject = serializers.CharField(max_length=150)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )

    def validate_subject(self, value):
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError("Use 5 to 150 characters.")
        return value


class CommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=2000)

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Write a comment.")
        return value


class StaffUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "platform_role"]


class AdminChangeRequestSerializer(ChangeRequestSerializer):
    tenant_id = serializers.UUIDField()
    tenant_name = serializers.CharField(source="tenant.name")
    requested_by_email = serializers.EmailField(source="requested_by.email")
    assignee = StaffUserSerializer(allow_null=True, read_only=True)

    class Meta(ChangeRequestSerializer.Meta):
        fields = [
            *ChangeRequestSerializer.Meta.fields,
            "tenant_id",
            "tenant_name",
            "requested_by_email",
            "assignee",
        ]


class AdminChangeRequestDetailSerializer(AdminChangeRequestSerializer):
    comments = StaffCommentSerializer(many=True, read_only=True)

    class Meta(AdminChangeRequestSerializer.Meta):
        fields = [*AdminChangeRequestSerializer.Meta.fields, "comments"]


class AdminChangeRequestUpdateSerializer(serializers.Serializer):
    """Assign (or unassign with null) and/or Start work (`status = in_progress`)."""

    assignee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), allow_null=True, required=False
    )
    status = serializers.ChoiceField(choices=["in_progress"], required=False)


class DeclineSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)

    def validate_reason(self, value):
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError("Give a reason (5 to 500 characters).")
        return value


class LinkVersionSerializer(serializers.Serializer):
    version = serializers.UUIDField()


class CandidateVersionSerializer(LinkedVersionSerializer):
    """A template version of the request's client that can be linked."""


def candidate_versions(tenant_id):
    from apps.messaging_templates.services import OPEN_STATUSES

    return (
        WhatsAppTemplateVersion.objects.filter(tenant_id=tenant_id, status__in=OPEN_STATUSES)
        .select_related("template")
        .order_by("template__name", "template__language", "-version")
    )

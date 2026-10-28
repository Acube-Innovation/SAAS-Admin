from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.core.validators import normalize_indian_mobile

from .models import StaffMember


class StaffMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffMember
        fields = ["id", "name", "phone", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError(_("At least 2 characters."))
        clash = StaffMember.objects.filter(
            tenant_id=self.context["request"].tenant_id, name__iexact=value
        )
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("A staff member with this name already exists."))
        return value

    def validate_phone(self, value):
        return normalize_indian_mobile(value) if value.strip() else ""

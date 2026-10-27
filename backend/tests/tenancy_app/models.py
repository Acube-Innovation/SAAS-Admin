from django.db import models

from apps.core.models import TenantScopedModel


class Note(TenantScopedModel):
    body = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="open")

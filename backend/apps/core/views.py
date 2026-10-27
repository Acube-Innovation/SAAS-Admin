import logging

import redis
from django.conf import settings
from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_redis():
    client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.ping()
    finally:
        client.close()


class HealthView(APIView):
    """Liveness check used by Docker Compose, the load balancer and monitoring."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            200: inline_serializer(
                "Health",
                {
                    "status": serializers.CharField(),
                    "database": serializers.CharField(),
                    "redis": serializers.CharField(),
                },
            ),
            503: None,
        }
    )
    def get(self, request):
        checks = {}
        for name, check in (("database", check_database), ("redis", check_redis)):
            try:
                check()
                checks[name] = "ok"
            except Exception:
                logger.exception("Health check failed: %s", name)
                checks[name] = "error"

        healthy = all(result == "ok" for result in checks.values())
        return Response(
            {"status": "ok" if healthy else "error", **checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

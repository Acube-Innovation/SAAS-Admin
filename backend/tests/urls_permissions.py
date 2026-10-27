"""Test-only URLs: one view per permission class, on top of the real URLconf."""

from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsPlatformStaff, IsSuperAdmin, tenant_roles
from config.urls import urlpatterns as real_urlpatterns


def make_view(permission):
    class View(APIView):
        permission_classes = [permission]

        def get(self, request):
            return Response({"ok": True, "tenant_id": request.tenant_id})

    return View.as_view()


urlpatterns = [
    *real_urlpatterns,
    path("t/staff", make_view(IsPlatformStaff)),
    path("t/super-admin", make_view(IsSuperAdmin)),
    path("t/owner", make_view(tenant_roles("owner"))),
    path("t/owner-or-manager", make_view(tenant_roles("owner", "manager"))),
    path("t/any-member", make_view(tenant_roles("owner", "manager", "office_staff"))),
]

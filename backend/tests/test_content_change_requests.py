"""
Messages and voice (portal spec 2.6), approvals and change requests (Workflow 3.3 in the portal,
admin spec 2.9 / Workflow 3.6). Clients have no create or edit route for content.
"""

import pytest
from django.core import mail
from django.urls import get_resolver
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.change_requests.models import ChangeRequest
from apps.core.tenancy import db_context, tenant_context
from apps.industry.services import assign_template
from apps.messaging_templates import services as wa
from apps.messaging_templates.models import (
    VersionStatus,
    VoiceScript,
    VoiceScriptVersion,
    WhatsAppTemplateVersion,
)
from apps.messaging_templates.providers import StubProvider
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
S = VersionStatus
CONTENT = "/api/portal/content/templates"
REQUESTS = "/api/portal/change-requests"
ADMIN = "/api/admin/change-requests"
BODY = "Hello {{customer_name}}, your {{cylinder_type}} cylinder comes on {{scheduled_date}}."
SAMPLES = {"customer_name": "Asha", "cylinder_type": "14.2 kg", "scheduled_date": "29 Oct 2026"}
BUTTONS = [
    {"text": "Yes, available", "outcome": "confirmed"},
    {"text": "Not available", "outcome": "not_available"},
]


@pytest.fixture
def delivery_type(gas_template):
    return gas_template.schedule_types.get(key="cylinder_delivery")


def make_template(tenant, delivery_type, actor, name="delivery_reminder", language="en"):
    return wa.create_template(
        tenant,
        {
            "name": name,
            "language": language,
            "schedule_type": delivery_type,
            "body": BODY,
            "buttons": BUTTONS,
            "sample_values": SAMPLES,
        },
        actor,
    )


def make_live(version, actor):
    """Take a draft all the way to live through the real transitions and the stub provider."""
    version = wa.send_to_client(version, actor)
    version = wa.client_approve(version, actor)
    version = wa.submit(version, actor)
    StubProvider.set_status(version.provider_template_id, "approved")
    version = wa.sync_status(version)
    return wa.make_live(version, actor)


def reload(version):
    with tenant_context(version.tenant_id):
        return WhatsAppTemplateVersion.objects.get(pk=version.pk)


def staff_client(user):
    client = APIClient()
    sign_in(client, user)
    return client


@pytest.fixture
def live(client_tenant, delivery_type, super_admin):
    return make_live(make_template(client_tenant, delivery_type, super_admin), super_admin)


@pytest.fixture
def for_review(live, super_admin):
    """Version 2 of the live template, sent to the client."""
    return wa.send_to_client(wa.new_version(live, super_admin), super_admin)


# --- view-only content ------------------------------------------------------------------------


def test_templates_show_live_and_pending_versions(portal, for_review, delivery_type, super_admin):
    make_template(for_review.tenant, delivery_type, super_admin, name="draft_only")
    rows = portal("office_staff").get(CONTENT).json()
    assert [r["name"] for r in rows] == ["delivery_reminder"]  # drafts stay with staff
    row = rows[0]
    assert row["live"]["version"] == 1 and row["live"]["is_live"] is True
    assert row["live"]["body"] == BODY and row["live"]["sample_values"] == SAMPLES
    assert row["pending"]["version"] == 2 and row["pending"]["can_review"] is True
    assert [e["action"] for e in row["history"]][:2] == ["send_to_client", "make_live"]
    assert all(e["by"] is None for e in row["history"] if e["action"] == "send_to_client")


def test_other_agencies_content_is_not_visible(portal, delivery_type, super_admin, gas_template):
    other = factories.make_tenant("Elsewhere Gas")
    assign_template(other, gas_template)
    other.save()
    theirs = make_live(make_template(other, delivery_type, super_admin), super_admin)
    api = portal("owner")
    assert api.get(CONTENT).json() == []
    assert api.get(f"{CONTENT}/{theirs.template_id}").status_code == 404


def test_manager_approves_a_version(portal, for_review):
    api = portal("manager")
    url = f"{CONTENT}/{for_review.template_id}/versions/{for_review.pk}/approve"
    data = api.post(url).json()
    assert data["status"] == "signed_off" and data["can_review"] is False
    assert data["signoff_method"] == "client" and data["signed_off_by_name"]
    version = reload(for_review)
    assert version.signed_off_by.email.startswith("manager-") and version.signed_off_at
    with db_context(platform=True):
        entry = AuditLog.objects.filter(object_id=str(version.pk)).latest("created_at")
    assert entry.after["action"] == "client_approve"
    # Approving twice is not a valid transition.
    assert api.post(url).status_code == 409


def test_send_back_needs_a_comment(portal, for_review):
    api = portal("owner")
    url = f"{CONTENT}/{for_review.template_id}/versions/{for_review.pk}/send-back"
    assert "comment" in api.post(url, {"comment": "  no "}, format="json").json()["fields"]
    assert "comment" in api.post(url, {"comment": "x" * 1001}, format="json").json()["fields"]
    data = api.post(url, {"comment": "Please mention the agency name"}, format="json").json()
    assert data["status"] == "sent_back"
    assert data["sent_back_comment"] == "Please mention the agency name"
    pending = api.get(f"{CONTENT}/{for_review.template_id}").json()["pending"]
    assert pending["status"] == "sent_back" and pending["can_review"] is False


def test_office_staff_and_support_cannot_review(portal, for_review, api, super_admin):
    url = f"{CONTENT}/{for_review.template_id}/versions/{for_review.pk}/approve"
    assert portal("office_staff").post(url).status_code == 403

    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    support = APIClient()
    sign_in(support, super_admin)
    session_obj = start_support_session(super_admin, for_review.tenant, "Checking templates")
    session = support.session
    session[SUPPORT_SESSION_KEY] = str(session_obj.pk)
    session.save()
    assert support.get(CONTENT).status_code == 200
    assert support.post(url).status_code == 403
    assert reload(for_review).status == S.SENT_TO_CLIENT


def test_cannot_review_a_version_not_sent(portal, live, super_admin):
    draft = wa.new_version(live, super_admin)
    api = portal("owner")
    # A draft isn't visible, and the live version can't be approved again.
    assert api.post(f"{CONTENT}/{live.template_id}/versions/{draft.pk}/approve").status_code == 409
    assert api.post(f"{CONTENT}/{live.template_id}/versions/{live.pk}/approve").status_code == 409


@pytest.mark.parametrize("role", ["owner", "manager", "office_staff"])
def test_content_write_attempts_are_refused(portal, live, role):
    api = portal(role)
    body = {"name": "my_template", "language": "en", "body": "Hello"}
    detail = f"{CONTENT}/{live.template_id}"
    version = f"{detail}/versions/{live.pk}"
    attempts = [
        api.post(CONTENT, body, format="json"),
        api.put(detail, body, format="json"),
        api.patch(detail, {"body": "Changed"}, format="json"),
        api.delete(detail),
        api.patch(version, {"body": "Changed"}, format="json"),
        api.post("/api/portal/content/voice-scripts", {"greeting": "Hi"}, format="json"),
        api.post("/api/portal/content/question-flows", {"answers": []}, format="json"),
        api.put("/api/portal/content/question-flows", {"answers": []}, format="json"),
        # The admin editor's API is for platform staff only.
        api.post("/api/admin/whatsapp-templates", body, format="json"),
        api.patch(f"/api/admin/whatsapp-templates/{detail.rsplit('/', 1)[1]}/versions/{live.pk}"),
    ]
    assert all(r.status_code in (403, 404, 405) for r in attempts), [
        r.status_code for r in attempts
    ]
    # Routes that exist refuse writes with 403 or 405; the version path has no route at all (404).
    assert {r.status_code for i, r in enumerate(attempts[:8]) if i != 4} <= {403, 405}
    assert reload(live).body == BODY


def test_portal_has_no_content_write_route():
    """Every portal route under content/ is read-only, apart from approve and send back."""
    portal_urls = next(p for p in get_resolver().url_patterns if str(p.pattern) == "api/portal/")
    found = []
    for pattern in portal_urls.url_patterns:
        route = str(pattern.pattern)
        if not route.startswith("^content/") and not route.startswith("content/"):
            continue
        callback = pattern.callback
        actions = getattr(callback, "actions", None)
        if actions is not None:  # a viewset route: HTTP method -> action
            writes = {m for m in actions if m not in ("get", "head", "options")}
            if writes:
                assert set(actions.values()) <= {"approve", "send_back"}, route
                found.append(route)
        else:
            view = callback.view_class
            assert not {"post", "put", "patch", "delete"} & set(dir(view)), route
    assert len(found) == 2  # approve and send-back


def test_voice_scripts_and_question_flows(portal, client_tenant, delivery_type):
    with tenant_context(client_tenant):
        script = VoiceScript.objects.create(
            tenant=client_tenant, name="delivery_call", language="ml", schedule_type=delivery_type
        )
        for number, status in [(1, "live"), (2, "draft")]:
            VoiceScriptVersion.objects.create(
                tenant=client_tenant,
                script=script,
                version=number,
                status=status,
                greeting=f"Namaskaram v{number}",
                question="Will someone be home?",
                answers=[{"text": "Yes", "outcome": "confirmed"}],
                voice="ml-IN-female",
            )
        VoiceScript.objects.create(
            tenant=client_tenant, name="draft_only", language="en", schedule_type=delivery_type
        )
    api = portal("office_staff")
    scripts = api.get("/api/portal/content/voice-scripts").json()
    assert [s["name"] for s in scripts] == ["delivery_call"]
    assert scripts[0]["live"]["greeting"] == "Namaskaram v1"
    assert scripts[0]["live"]["max_retries"] == 2 and scripts[0]["live"]["retry_gap_minutes"] == 60
    flows = api.get("/api/portal/content/question-flows").json()
    assert flows[0]["schedule_type"] == "cylinder_delivery"
    assert [a["outcome"] for a in flows[0]["answers"]] == [
        "confirmed",
        "not_available",
        "reschedule",
    ]


# --- change requests: portal ------------------------------------------------------------------


def raise_request(api, **overrides):
    body = {
        "request_type": "edit_template",
        "subject": "Add the agency phone number",
        "description": "Customers ask where to call. Please add 0484 2345678 to the footer.",
        **overrides,
    }
    return api.post(REQUESTS, body, format="json")


def test_owner_raises_a_request_and_staff_are_emailed(
    portal, client_tenant, super_admin, support_user, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = raise_request(portal("owner"))
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == "open" and data["comments"] == [] and data["requested_by_name"]
    assert len(mail.outbox) == 1
    assert set(mail.outbox[0].to) == {super_admin.email, support_user.email}
    assert f"/change-requests/{data['id']}" in mail.outbox[0].body
    with db_context(platform=True):
        assert AuditLog.objects.filter(
            object_type="change_requests.changerequest", action="create", tenant=client_tenant
        ).exists()


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"subject": "abc "}, "subject"),
        ({"subject": "x" * 151}, "subject"),
        ({"description": "x" * 2001}, "description"),
        ({"request_type": "rewrite_everything"}, "request_type"),
    ],
)
def test_request_rules(portal, overrides, field):
    response = raise_request(portal("manager"), **overrides)
    assert response.status_code == 400 and field in response.json()["fields"]


def test_office_staff_view_requests_but_cannot_raise_or_comment(portal):
    request_id = raise_request(portal("owner")).json()["id"]
    api = portal("office_staff")
    assert api.get(REQUESTS).json()["count"] == 1
    assert raise_request(api).status_code == 403
    assert api.post(f"{REQUESTS}/{request_id}/comments", {"body": "Hi"}).status_code == 403


def test_clients_cannot_edit_or_delete_requests(portal):
    api = portal("owner")
    request_id = raise_request(api).json()["id"]
    assert (
        api.patch(f"{REQUESTS}/{request_id}", {"status": "done"}, format="json").status_code == 405
    )
    assert api.delete(f"{REQUESTS}/{request_id}").status_code == 405
    assert api.post(f"{ADMIN}/{request_id}/decline", {"reason": "nope nope"}).status_code == 403


def test_requests_are_isolated_per_agency(portal, client_tenant):
    request_id = raise_request(portal("owner")).json()["id"]
    other = factories.make_tenant("Other Agency")
    api = portal("owner", tenant=other)
    assert api.get(REQUESTS).json()["count"] == 0
    assert api.get(f"{REQUESTS}/{request_id}").status_code == 404
    assert api.post(f"{REQUESTS}/{request_id}/comments", {"body": "Hi"}).status_code == 404


# --- change requests: admin queue -------------------------------------------------------------


def test_queue_filters_and_dashboard_count(portal, super_admin, support_user):
    client_api = portal("owner")
    first = raise_request(client_api).json()["id"]
    raise_request(client_api, request_type="new_language", subject="Add Hindi please").json()
    admin = staff_client(super_admin)
    assert admin.get(ADMIN).json()["count"] == 2
    assert admin.get(ADMIN, {"request_type": "new_language"}).json()["count"] == 1
    assert admin.get(ADMIN, {"assignee": "none"}).json()["count"] == 2
    admin.patch(f"{ADMIN}/{first}", {"assignee": str(support_user.pk)}, format="json")
    assert admin.get(ADMIN, {"assignee": str(support_user.pk)}).json()["count"] == 1
    assert admin.get(ADMIN, {"search": "hindi"}).json()["count"] == 1
    assert admin.get("/api/admin/dashboard").json()["open_change_requests"] == 2
    admin.post(f"{ADMIN}/{first}/decline", {"reason": "Footer can't hold a phone number"})
    assert admin.get(ADMIN, {"status": "active"}).json()["count"] == 1
    assert admin.get("/api/admin/dashboard").json()["open_change_requests"] == 1
    assignees = admin.get(f"{ADMIN}/assignees").json()
    assert {a["email"] for a in assignees} == {super_admin.email, support_user.email}


def test_support_works_a_request_and_client_sees_replies(portal, support_user):
    client_api = portal("owner")
    request_id = raise_request(client_api).json()["id"]
    support = staff_client(support_user)
    data = support.patch(f"{ADMIN}/{request_id}", {"status": "in_progress"}, format="json").json()
    assert data["status"] == "in_progress" and data["started_at"]
    assert data["assignee"]["email"] == support_user.email  # starting assigns yourself
    # Start work only from open.
    assert (
        support.patch(f"{ADMIN}/{request_id}", {"status": "in_progress"}, format="json").status_code
        == 409
    )
    support.post(f"{ADMIN}/{request_id}/comments", {"body": "Which footer line?"}, format="json")
    client_api.post(
        f"{REQUESTS}/{request_id}/comments", {"body": "The second one, please"}, format="json"
    )
    comments = client_api.get(f"{REQUESTS}/{request_id}").json()["comments"]
    assert [(c["from_staff"], c["body"]) for c in comments] == [
        (True, "Which footer line?"),
        (False, "The second one, please"),
    ]
    assert "author_email" not in comments[0]  # staff addresses stay internal
    admin_comments = support.get(f"{ADMIN}/{request_id}").json()["comments"]
    assert admin_comments[0]["author_email"] == support_user.email


def test_decline_needs_a_reason_and_closes_the_thread(portal, support_user):
    client_api = portal("owner")
    request_id = raise_request(client_api).json()["id"]
    support = staff_client(support_user)
    assert (
        "reason" in support.post(f"{ADMIN}/{request_id}/decline", {"reason": "no"}).json()["fields"]
    )
    data = support.post(f"{ADMIN}/{request_id}/decline", {"reason": "Meta won't allow it"}).json()
    assert data["status"] == "declined" and data["closed_at"]
    detail = client_api.get(f"{REQUESTS}/{request_id}").json()
    assert detail["decline_reason"] == "Meta won't allow it"
    response = client_api.post(f"{REQUESTS}/{request_id}/comments", {"body": "Why?"})
    assert response.status_code == 409 and response.json()["code"] == "request_closed"
    assert support.patch(f"{ADMIN}/{request_id}", {"status": "in_progress"}).status_code == 409


def test_assignee_must_be_platform_staff(portal, super_admin):
    request_id = raise_request(portal("owner")).json()["id"]
    outsider = factories.make_user("not-staff@agency.test")
    admin = staff_client(super_admin)
    response = admin.patch(f"{ADMIN}/{request_id}", {"assignee": str(outsider.pk)}, format="json")
    assert response.status_code == 400
    response = admin.patch(f"{ADMIN}/{request_id}", {"assignee": None}, format="json")
    assert response.status_code == 200 and response.json()["assignee"] is None


def test_link_version_rules(portal, live, super_admin, delivery_type, gas_template):
    request_id = raise_request(portal("owner")).json()["id"]
    admin = staff_client(super_admin)
    url = f"{ADMIN}/{request_id}/link-version"
    # A live version is not "in progress".
    assert admin.post(url, {"version": str(live.pk)}).status_code == 400
    other = factories.make_tenant("Elsewhere Gas")
    assign_template(other, gas_template)
    other.save()
    theirs = make_template(other, delivery_type, super_admin)
    assert admin.post(url, {"version": str(theirs.pk)}).status_code == 400
    draft = wa.new_version(live, super_admin)
    assert [v["id"] for v in admin.get(f"{ADMIN}/{request_id}/versions").json()] == [str(draft.pk)]
    data = admin.post(url, {"version": str(draft.pk)}).json()
    assert data["linked_version"]["version"] == 2 and data["linked_version"]["status"] == "draft"


def test_portal_users_cannot_reach_the_admin_queue(portal):
    api = portal("owner")
    assert api.get(ADMIN).status_code == 403


# --- Workflow 3.3 / 3.6 end to end ------------------------------------------------------------


def test_workflow_3_3_end_to_end(portal, live, super_admin, support_user):
    # 1. The client raises a change request.
    client_api = portal("manager")
    request_id = raise_request(client_api).json()["id"]

    # 2. Support assigns it, starts work, drafts version 2 and links it.
    support = staff_client(support_user)
    support.patch(f"{ADMIN}/{request_id}", {"status": "in_progress"}, format="json")
    base = f"/api/admin/whatsapp-templates/{live.template_id}/versions"
    draft = support.post(f"{base}/{live.pk}/new-version").json()
    support.patch(f"{base}/{draft['id']}", {"footer": "Call 0484 2345678 for help."}, format="json")
    support.post(f"{ADMIN}/{request_id}/link-version", {"version": draft["id"]})

    # 3. Support sends it to the client; the client sends it back, support edits and resends.
    assert support.post(f"{base}/{draft['id']}/send-to-client").json()["status"] == "sent_to_client"
    portal_version = f"{CONTENT}/{live.template_id}/versions/{draft['id']}"
    client_api.post(f"{portal_version}/send-back", {"comment": "Use the office number"})
    support.patch(f"{base}/{draft['id']}", {"footer": "Call 0484 2300000 for help."})
    support.post(f"{base}/{draft['id']}/send-to-client")

    # 4. The client approves in the portal.
    pending = client_api.get(f"{CONTENT}/{live.template_id}").json()["pending"]
    assert pending["footer"] == "Call 0484 2300000 for help." and pending["can_review"]
    assert client_api.post(f"{portal_version}/approve").json()["status"] == "signed_off"

    # Support can't submit to Meta or make versions live.
    assert support.post(f"{base}/{draft['id']}/submit").status_code == 403

    # 5. A super admin submits; Meta approves; the version goes live.
    admin = staff_client(super_admin)
    provider_id = admin.post(f"{base}/{draft['id']}/submit").json()["provider_template_id"]
    StubProvider.set_status(provider_id, "approved")
    admin.post(f"{base}/{draft['id']}/sync-status")
    assert support.post(f"{base}/{draft['id']}/make-live").status_code == 403
    assert admin.post(f"{base}/{draft['id']}/make-live").json()["is_live"] is True

    # 6. The request is done and the client sees the new live version.
    detail = client_api.get(f"{REQUESTS}/{request_id}").json()
    assert detail["status"] == "done" and detail["closed_at"]
    template = client_api.get(f"{CONTENT}/{live.template_id}").json()
    assert template["live"]["version"] == 2 and template["pending"] is None
    assert template["live"]["footer"] == "Call 0484 2300000 for help."
    history = [e["action"] for e in template["history"]]
    assert history[:3] == ["make_live", "client_approve", "send_to_client"]
    with tenant_context(live.tenant_id):
        change_request = ChangeRequest.objects.get(pk=request_id)
    with db_context(platform=True):
        actions = list(
            AuditLog.objects.filter(object_id=str(change_request.pk))
            .order_by("created_at")
            .values_list("action", flat=True)
        )
    assert actions[0] == "create" and actions[-1] == "status_change"
    assert len(actions) >= 4  # create, start work, link, done

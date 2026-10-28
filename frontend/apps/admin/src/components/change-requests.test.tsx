import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { mockApi, navigation, renderWithQuery } from "@/test/utils";

import { ChangeRequestDetail } from "./change-requests/detail";
import { ChangeRequestQueue } from "./change-requests/queue";

const STAFF = [
  { id: "u-sa", email: "presenter@acube.test", full_name: "Priya Admin", platform_role: "super_admin" },
  { id: "u-su", email: "support.demo@acube.test", full_name: "Sam Support", platform_role: "support" },
];

function request(overrides: Record<string, unknown> = {}) {
  return {
    id: "cr1", request_type: "edit_template", subject: "Add our phone number", description: "Please add 0484 2345678.",
    status: "open", requested_by_name: "Olivia Owner", linked_version: null, decline_reason: "", comment_count: 1,
    created_at: "2026-10-28T05:00:00Z", updated_at: "2026-10-28T06:00:00Z", started_at: null, closed_at: null,
    tenant_id: "t1", tenant_name: "Kochi Gas Agency", requested_by_email: "owner@kochi.test", assignee: null,
    ...overrides,
  };
}
const detail = (overrides: Record<string, unknown> = {}) => ({
  ...request(overrides),
  comments: [{ id: "c1", author_name: "Olivia Owner", author_email: "owner@kochi.test", from_staff: false, body: "Any update?", created_at: "2026-10-28T06:00:00Z" }],
});
const page = (results: unknown[]) => ({ count: results.length, next: null, previous: null, results });

beforeEach(() => {
  navigation.pathname = "/change-requests";
  navigation.search = new URLSearchParams();
});

describe("Change requests queue", () => {
  it("lists open and in-progress requests by default and filters", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/admin/change-requests", body: page([request(), request({ id: "cr2", subject: "Add Hindi", status: "in_progress", assignee: STAFF[1] })]) },
      { method: "GET", path: "/api/admin/tenants", body: page([{ id: "t1", name: "Kochi Gas Agency" }]) },
      { method: "GET", path: "/api/admin/change-requests/assignees", body: STAFF },
    ]);
    renderWithQuery(<ChangeRequestQueue />);
    expect(await screen.findByRole("link", { name: /Add our phone number/ })).toHaveAttribute("href", "/change-requests/cr1");
    const row = screen.getByRole("link", { name: /Add Hindi/ }).closest("tr")!;
    expect(row).toHaveTextContent("Sam Support");
    expect(row).toHaveTextContent("In progress");
    const first = calls.find((c) => c.path === "/api/admin/change-requests")!;
    expect(new URLSearchParams(first.search).get("status")).toBe("active");
    expect(screen.getByLabelText("Status")).toHaveValue("active");
  });

  it("asks the API for the filters in the address bar", async () => {
    navigation.search = new URLSearchParams("status=&assignee=me&request_type=new_language");
    const calls = mockApi([
      { method: "GET", path: "/api/admin/change-requests", body: page([]) },
      { method: "GET", path: "/api/admin/tenants", body: page([]) },
      { method: "GET", path: "/api/admin/change-requests/assignees", body: STAFF },
    ]);
    renderWithQuery(<ChangeRequestQueue />);
    expect(await screen.findByText("No change requests match these filters.")).toBeInTheDocument();
    const query = new URLSearchParams(calls.find((c) => c.path === "/api/admin/change-requests")!.search);
    expect([query.get("status"), query.get("assignee"), query.get("request_type")]).toEqual([null, "me", "new_language"]);
  });
});

describe("Change request detail", () => {
  it("starts work, replies and links a version", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/admin/change-requests/cr1", body: detail() },
      { method: "GET", path: "/api/admin/change-requests/assignees", body: STAFF },
      {
        method: "GET", path: "/api/admin/change-requests/cr1/versions",
        body: [{ id: "v2", template_id: "wt1", template_name: "cylinder_delivery_reminder", language: "en", version: 2, status: "draft", is_live: false }],
      },
      { method: "GET", path: "/api/auth/csrf", status: 204 },
      { method: "PATCH", path: "/api/admin/change-requests/cr1", body: detail({ status: "in_progress" }) },
      { method: "POST", path: "/api/admin/change-requests/cr1/comments", status: 201, body: detail() },
      { method: "POST", path: "/api/admin/change-requests/cr1/link-version", body: detail() },
    ]);
    renderWithQuery(<ChangeRequestDetail id="cr1" />);
    expect(await screen.findByText("owner@kochi.test (client)", { exact: false })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Start work" }));
    expect(await screen.findByText("Started. The client sees it as in progress.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "in_progress" });

    await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
    expect(await screen.findByText("Write a reply.")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: /Reply to the client/ }), "Working on it today.");
    await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/comments"))?.body).toEqual({ body: "Working on it today." }));

    const linked = screen.getByRole("region", { name: "Linked template version" });
    await userEvent.selectOptions(within(linked).getByLabelText("Version to link"), "v2");
    await userEvent.click(within(linked).getByRole("button", { name: "Link version" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/link-version"))?.body).toEqual({ version: "v2" }));
  });

  it("assigns and declines with a reason", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/admin/change-requests/cr1", body: detail() },
      { method: "GET", path: "/api/admin/change-requests/assignees", body: STAFF },
      { method: "GET", path: "/api/admin/change-requests/cr1/versions", body: [] },
      { method: "GET", path: "/api/auth/csrf", status: 204 },
      { method: "PATCH", path: "/api/admin/change-requests/cr1", body: detail({ assignee: STAFF[1] }) },
      { method: "POST", path: "/api/admin/change-requests/cr1/decline", body: detail({ status: "declined" }) },
    ]);
    renderWithQuery(<ChangeRequestDetail id="cr1" />);
    await userEvent.selectOptions(await screen.findByLabelText("Assignee"), "u-su");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ assignee: "u-su" }));
    expect(screen.getByText(/no template version in progress/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    const dialog = await screen.findByRole("dialog", { name: "Decline this request?" });
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "no");
    await userEvent.click(within(dialog).getByRole("button", { name: "Decline" }));
    expect(await within(dialog).findByText("Give a reason of at least 5 characters.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "t possible with Meta");
    await userEvent.click(within(dialog).getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/decline"))?.body).toEqual({ reason: "not possible with Meta" }));
  });

  it("a done request links to its live version and takes no replies", async () => {
    mockApi([
      {
        method: "GET", path: "/api/admin/change-requests/cr1",
        body: detail({ status: "done", closed_at: "2026-10-29T05:00:00Z",
          linked_version: { id: "v2", template_id: "wt1", template_name: "cylinder_delivery_reminder", language: "en", version: 2, status: "approved", is_live: true } }),
      },
      { method: "GET", path: "/api/admin/change-requests/assignees", body: STAFF },
    ]);
    renderWithQuery(<ChangeRequestDetail id="cr1" />);
    expect(await screen.findByRole("link", { name: "Open in the template editor" })).toHaveAttribute("href", "/whatsapp-templates/wt1?v=v2");
    expect(screen.getByText(/EN · v2 · Live/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send reply" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Decline" })).not.toBeInTheDocument();
  });
});

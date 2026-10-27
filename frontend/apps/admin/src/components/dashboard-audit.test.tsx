import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { mockApi, navigation, renderWithQuery, router } from "@/test/utils";

import { AuditLog } from "./audit/audit-log";
import { Dashboard } from "./dashboard/dashboard";

function dashboard(overrides: Record<string, unknown> = {}) {
  return {
    clients_active: 12,
    clients_suspended: 2,
    messages_today: { sent: 0, delivered: 0, failed: 0, replied: 0 },
    messages_live: false,
    quality_rating: "GREEN",
    quality_alert: false,
    failed_webhooks_24h: 0,
    templates_awaiting_action: 3,
    open_change_requests: 0,
    active_support_sessions: 1,
    generated_at: "2026-10-27T04:45:00Z",
    ...overrides,
  };
}

function tile(label: string) {
  return screen.getAllByTestId("tile").find((el) => el.dataset.label === label)!;
}

describe("Dashboard", () => {
  it("shows the tiles, with message tiles at zero until Phase 2", async () => {
    mockApi([{ method: "GET", path: "/api/admin/dashboard", body: dashboard() }]);
    renderWithQuery(<Dashboard />);

    await waitFor(() => expect(tile("Active clients")).toHaveTextContent("12"));
    expect(tile("Suspended clients")).toHaveTextContent("2");
    expect(within(tile("Active clients")).getByRole("link")).toHaveAttribute("href", "/clients?status=active");
    for (const label of ["Sent", "Delivered", "Failed", "Replied"]) expect(tile(label)).toHaveTextContent("0");
    expect(screen.getAllByText("Live from Phase 2").length).toBeGreaterThan(0);
    expect(tile("WhatsApp number quality")).toHaveTextContent("High (green)");
    expect(tile("Templates awaiting action")).toHaveTextContent("3");
    expect(tile("Active support sessions")).toHaveTextContent("1");
    expect(screen.getByText(/Updated 27 Oct 2026/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("warns when the quality rating is red", async () => {
    mockApi([
      { method: "GET", path: "/api/admin/dashboard", body: dashboard({ quality_rating: "RED", quality_alert: true }) },
    ]);
    renderWithQuery(<Dashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent("quality rating is low (red)");
    expect(tile("WhatsApp number quality")).toHaveTextContent("Low (red)");
  });
});

const ENTRIES = [
  {
    id: "e2",
    created_at: "2026-10-21T18:25:00Z",
    tenant_id: "t1",
    tenant_name: "Kochi Gas Agency",
    actor_id: "u1",
    actor_email: "admin@acube.test",
    support_session_id: null,
    action: "update",
    object_type: "core.tenant",
    object_id: "t1",
    before: { city: "Kochi", status: "active" },
    after: { city: "Ernakulam", status: "active" },
    ip_address: "10.0.0.5",
    user_agent: "Mozilla/5.0",
  },
  {
    id: "e1",
    created_at: "2026-10-20T04:30:00Z",
    tenant_id: null,
    tenant_name: null,
    actor_id: "u2",
    actor_email: "support@acube.test",
    support_session_id: "s1",
    action: "support_start",
    object_type: "platform_admin.supportsession",
    object_id: "s1",
    before: null,
    after: { reason: "Checking templates" },
    ip_address: null,
    user_agent: "",
  },
];

const FACETS = {
  actions: ["support_start", "update"],
  object_types: ["core.tenant", "platform_admin.supportsession"],
  tenants: [{ id: "t1", name: "Kochi Gas Agency" }],
};

function auditApi(extra: Parameters<typeof mockApi>[0] = []) {
  return mockApi([
    { method: "GET", path: "/api/admin/audit-logs", body: { count: 2, next: null, previous: null, results: ENTRIES } },
    { method: "GET", path: "/api/admin/audit-logs/facets", body: FACETS },
    ...extra,
  ]);
}

describe("Audit log", () => {
  beforeEach(() => {
    navigation.pathname = "/audit-log";
    navigation.search = new URLSearchParams();
  });

  it("lists entries and sends the URL filters to the API", async () => {
    navigation.search = new URLSearchParams("action=update&tenant=t1&date_from=2026-10-20");
    const calls = auditApi();
    renderWithQuery(<AuditLog />);

    const table = await screen.findByRole("table", { name: "Audit log" });
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(within(table).getByText("Platform")).toBeInTheDocument();
    expect(within(table).getByText("Support session")).toBeInTheDocument();

    const list = calls.find((c) => c.path === "/api/admin/audit-logs")!;
    const query = new URLSearchParams(list.search);
    expect(Object.fromEntries(query)).toEqual({
      action: "update",
      tenant: "t1",
      date_from: "2026-10-20",
      page: "1",
      page_size: "50",
    });
    expect(screen.getByLabelText("Action")).toHaveValue("update");
    expect(screen.getByLabelText("Client")).toHaveValue("t1");
  });

  it("changing a filter updates the URL and resets the page", async () => {
    navigation.search = new URLSearchParams("page=3&actor=admin");
    auditApi();
    renderWithQuery(<AuditLog />);
    await screen.findByRole("option", { name: "core.tenant" });

    await userEvent.selectOptions(screen.getByLabelText("Record"), "core.tenant");
    expect(router.replace).toHaveBeenCalledWith("/audit-log?actor=admin&object_type=core.tenant");

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(router.replace).toHaveBeenLastCalledWith("/audit-log");
  });

  it("shows the before/after comparison with changed fields marked", async () => {
    auditApi();
    renderWithQuery(<AuditLog />);

    const [first] = await screen.findAllByRole("button", { name: "View" });
    await userEvent.click(first!);

    const dialog = screen.getByRole("dialog", { name: "Audit entry" });
    const changes = within(dialog).getByRole("table", { name: "Before and after" });
    const city = within(changes).getByRole("row", { name: /city/ });
    expect(city).toHaveAttribute("data-changed", "true");
    expect(city).toHaveTextContent("Kochi");
    expect(city).toHaveTextContent("Ernakulam");
    expect(within(changes).getByRole("row", { name: /status/ })).toHaveAttribute("data-changed", "false");
    expect(within(dialog).getByText("10.0.0.5")).toBeInTheDocument();
  });

  it("exports the CSV with the same filters and refreshes the list", async () => {
    navigation.search = new URLSearchParams("action=update");
    const createObjectURL = vi.fn(() => "blob:csv");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const calls = auditApi([
      {
        method: "GET",
        path: "/api/admin/audit-logs/export",
        raw: {
          text: "created_at,actor_email\n2026-10-21T23:55:00+05:30,admin@acube.test\n",
          headers: {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="audit-log-20261027-1015.csv"',
          },
        },
      },
    ]);
    renderWithQuery(<AuditLog />);
    await screen.findAllByRole("button", { name: "View" });

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Exported 2 entries");
    const exportCall = calls.find((c) => c.path === "/api/admin/audit-logs/export")!;
    expect(exportCall.search).toBe("?action=update");
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/admin/audit-logs")).toHaveLength(2));
    click.mockRestore();
  });

  it("shows why an export was refused", async () => {
    auditApi([
      {
        method: "GET",
        path: "/api/admin/audit-logs/export",
        status: 400,
        body: { code: "export_too_large", detail: "60000 entries match.", fields: {} },
      },
    ]);
    renderWithQuery(<AuditLog />);
    await screen.findAllByRole("button", { name: "View" });

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many entries to export at once");
  });
});

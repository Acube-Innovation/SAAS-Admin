import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { SupportBanner } from "@/components/support-banner";
import { INDUSTRIES, REFERENCE, tenant } from "@/test/fixtures";
import { authState, mockApi, navigation, renderWithQuery, router } from "@/test/utils";

import { ClientDetail } from "./client-detail";
import { ClientsList } from "./clients-list";
import { CreateClientForm } from "./create-client-form";

const ME = { method: "GET", path: "/api/auth/me", body: authState() };
const SUPPORT_ME = { method: "GET", path: "/api/auth/me", body: authState({ platform_role: "support" }) };
const COMMON = [
  { method: "GET", path: "/api/admin/reference-data", body: REFERENCE },
  { method: "GET", path: "/api/admin/industry-templates", body: INDUSTRIES },
  { method: "GET", path: "/api/admin/support-sessions/current", status: 204 },
];

beforeEach(() => {
  navigation.pathname = "/clients";
  navigation.search = new URLSearchParams();
});

describe("Clients list (spec 2.3)", () => {
  it("lists clients and links to their detail", async () => {
    mockApi([
      ME,
      ...COMMON,
      { method: "GET", path: "/api/admin/tenants", body: { count: 1, results: [tenant()] } },
    ]);
    renderWithQuery(<ClientsList />);

    const row = (await screen.findByText("Kochi Gas Agency")).closest("tr")!;
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(row).getByText("asha@kochigas.test")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Kochi Gas Agency/ })).toHaveAttribute("href", "/clients/t1");
    expect(screen.getByRole("button", { name: "New client" })).toBeInTheDocument();
  });

  it("puts filters in the URL and sends them to the API", async () => {
    navigation.search = new URLSearchParams("status=suspended&search=kochi");
    const calls = mockApi([ME, ...COMMON, { method: "GET", path: "/api/admin/tenants", body: { count: 0, results: [] } }]);
    renderWithQuery(<ClientsList />);

    expect(await screen.findByText("No clients match these filters.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Status"), "active");
    expect(router.replace).toHaveBeenCalledWith("/clients?search=kochi&status=active");
    expect(calls.some((c) => c.path === "/api/admin/tenants")).toBe(true);
  });

  it("hides New client from support staff", async () => {
    mockApi([SUPPORT_ME, ...COMMON, { method: "GET", path: "/api/admin/tenants", body: { count: 0, results: [] } }]);
    renderWithQuery(<ClientsList />);
    await screen.findByText("No clients yet. Create the first one.");
    expect(screen.queryByRole("button", { name: "New client" })).not.toBeInTheDocument();
  });
});

describe("Create client (spec 2.4)", () => {
  async function fill(overrides: Record<string, string> = {}) {
    const values: Record<string, string> = {
      "Display name": "Kollam Gas Agency",
      "Legal name": "Kollam Gas Agency LLP",
      GSTIN: "32ABCDE1234F1Z5",
      "Address line 1": "Chinnakada Road",
      City: "Kollam",
      "PIN code": "691001",
      "Contact name": "Suresh",
      "Contact mobile": "98470 12345",
      "Contact email": "office@kollamgas.test",
      "Owner name": "Suresh Pillai",
      "Owner email": "suresh@kollamgas.test",
      ...overrides,
    };
    const { State: state, ...textValues } = values;
    for (const [label, value] of Object.entries(textValues)) {
      const input = screen.getByLabelText(new RegExp(`^${label}`));
      await userEvent.clear(input);
      if (value) await userEvent.type(input, value);
    }
    await userEvent.selectOptions(screen.getByLabelText(/^State/), state ?? "KL");
  }

  it("checks the fields before calling the API, including GSTIN against the state", async () => {
    const calls = mockApi([ME, ...COMMON]);
    renderWithQuery(<CreateClientForm />);
    await screen.findByText("New client");

    await fill({ "PIN code": "091001", "Contact mobile": "12345", State: "KA" });
    await userEvent.click(screen.getByRole("button", { name: "Create client and invite owner" }));

    expect(await screen.findByText("This GSTIN does not belong to the selected state.")).toBeInTheDocument();
    expect(screen.getByText("Enter a 6-digit PIN code that doesn't start with 0.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid Indian mobile number.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows server errors on the right field", async () => {
    mockApi([
      ME,
      ...COMMON,
      {
        method: "POST",
        path: "/api/admin/tenants",
        status: 400,
        body: { code: "validation_error", detail: "Please correct the highlighted fields.", fields: { gstin: ["Another client already uses this GSTIN."] } },
      },
    ]);
    renderWithQuery(<CreateClientForm />);
    await screen.findByText("New client");
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Create client and invite owner" }));

    expect(await screen.findByText("Another client already uses this GSTIN.")).toBeInTheDocument();
    expect(screen.getByLabelText(/^GSTIN/)).toHaveAttribute("aria-invalid", "true");
  });

  it("creates the client with normalised values and opens its Users tab", async () => {
    const calls = mockApi([ME, ...COMMON, { method: "POST", path: "/api/admin/tenants", status: 201, body: tenant({ id: "new-id" }) }]);
    renderWithQuery(<CreateClientForm />);
    await screen.findByText("New client");
    await fill({ GSTIN: "32abcde1234f1z5" });
    await userEvent.click(screen.getByRole("button", { name: "Create client and invite owner" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/clients/new-id?tab=users&created=1"));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      industry_template: "tpl-gas",
      gstin: "32ABCDE1234F1Z5",
      contact_phone: "+919847012345",
      daily_send_cap: 5000,
      default_language: "ml",
      owner_phone: "",
    });
  });

  it("is refused to support staff", async () => {
    mockApi([SUPPORT_ME, ...COMMON]);
    renderWithQuery(<CreateClientForm />);
    expect(await screen.findByText("Not available for this account")).toBeInTheDocument();
  });
});

describe("Client detail (spec 2.5)", () => {
  it("shows the read-only banner for a suspended client and offers Activate", async () => {
    mockApi([
      ME,
      ...COMMON,
      {
        method: "GET",
        path: "/api/admin/tenants/t1",
        body: tenant({ status: "suspended", suspended_at: "2026-10-27T09:00:00Z", suspension_reason: "Payment overdue" }),
      },
    ]);
    renderWithQuery(<ClientDetail id="t1" />);

    expect(await screen.findByText(/Reason: Payment overdue\. The client's users have read-only access/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
  });

  it("suspends with a reason", async () => {
    const calls = mockApi([
      ME,
      ...COMMON,
      { method: "GET", path: "/api/admin/tenants/t1", body: tenant() },
      { method: "POST", path: "/api/admin/tenants/t1/suspend", body: tenant({ status: "suspended", suspended_at: "2026-10-27T09:00:00Z", suspension_reason: "Payment overdue" }) },
    ]);
    renderWithQuery(<ClientDetail id="t1" />);

    await userEvent.click(await screen.findByRole("button", { name: "Suspend" }));
    const dialog = screen.getByRole("dialog", { name: "Suspend Kochi Gas Agency?" });
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "bad");
    await userEvent.click(within(dialog).getByRole("button", { name: "Suspend" }));
    expect(await within(dialog).findByText("Give a reason of at least 5 characters.")).toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText(/Reason/), " payment overdue");
    await userEvent.click(within(dialog).getByRole("button", { name: "Suspend" }));

    expect(await screen.findByText("Client suspended.")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/suspend"))?.body).toEqual({ reason: "bad payment overdue" });
    expect(screen.getByRole("button", { name: "Activate" })).toBeInTheDocument();
  });

  it("support staff can open support access but not suspend or edit", async () => {
    const calls = mockApi([
      SUPPORT_ME,
      ...COMMON,
      { method: "GET", path: "/api/admin/tenants/t1", body: tenant() },
      {
        method: "POST",
        path: "/api/admin/tenants/t1/support-sessions",
        status: 201,
        body: { id: "s1", tenant_name: "Kochi Gas Agency", expires_at: new Date(Date.now() + 3600_000).toISOString() },
      },
    ]);
    renderWithQuery(<ClientDetail id="t1" />);

    await screen.findByRole("button", { name: "Open as support" });
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open as support" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Owner can't see bookings");
    await userEvent.click(within(dialog).getByRole("button", { name: "Open as support" }));

    expect(await screen.findByText("Support session started for Kochi Gas Agency.")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/support-sessions"))?.body).toEqual({ reason: "Owner can't see bookings" });
  });

  it("switches tabs through the URL and shows the client's reminder rules", async () => {
    navigation.pathname = "/clients/t1";
    navigation.search = new URLSearchParams("tab=settings");
    mockApi([ME, ...COMMON, { method: "GET", path: "/api/admin/tenants/t1", body: tenant() }]);
    renderWithQuery(<ClientDetail id="t1" />);

    expect(await screen.findByRole("tab", { name: "Settings" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("10:00")).toBeInTheDocument();
    expect(screen.getByText("240 minutes")).toBeInTheDocument();
    expect(screen.getByText("Mon, Tue, Wed, Thu, Fri, Sat")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Users" }));
    expect(router.replace).toHaveBeenCalledWith("/clients/t1?tab=users");
  });

  it("resends an invite from the Users tab", async () => {
    navigation.search = new URLSearchParams("tab=users");
    const calls = mockApi([
      ME,
      ...COMMON,
      { method: "GET", path: "/api/admin/tenants/t1", body: tenant() },
      {
        method: "GET",
        path: "/api/admin/tenants/t1/users",
        body: [{ id: "m1", email: "asha@kochigas.test", full_name: "", role: "owner", status: "invited", last_login: null, otp_enabled: false }],
      },
      { method: "POST", path: "/api/admin/tenants/t1/users/m1/resend-invite", status: 204 },
    ]);
    renderWithQuery(<ClientDetail id="t1" />);

    await userEvent.click(await screen.findByRole("button", { name: "Resend invite" }));
    expect(await screen.findByText(/A new invitation was sent to asha@kochigas.test/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/resend-invite"))).toBe(true);
  });
});

describe("Support banner (Workflow 3.3)", () => {
  it("shows the client and time left, and ends the session", async () => {
    const calls = mockApi([
      {
        method: "GET",
        path: "/api/admin/support-sessions/current",
        body: { id: "s1", tenant_name: "Kochi Gas Agency", expires_at: new Date(Date.now() + 59 * 60_000 + 30_000).toISOString() },
      },
      { method: "POST", path: "/api/admin/support-sessions/s1/end", body: {} },
    ]);
    renderWithQuery(<SupportBanner />);

    expect(await screen.findByText("Support mode: viewing Kochi Gas Agency, read-only.")).toBeInTheDocument();
    expect(screen.getByText(/Time left 59:(29|30)/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "End session" }));
    await waitFor(() => expect(screen.queryByText(/Support mode/)).not.toBeInTheDocument());
    expect(calls.some((c) => c.path.endsWith("/end"))).toBe(true);
  });

  it("is hidden without a session", async () => {
    mockApi([{ method: "GET", path: "/api/admin/support-sessions/current", status: 204 }]);
    renderWithQuery(<SupportBanner />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(/Support mode/)).not.toBeInTheDocument();
  });
});

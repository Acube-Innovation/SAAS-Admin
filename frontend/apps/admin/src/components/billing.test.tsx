import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { authState, mockApi, renderWithQuery } from "@/test/utils";

import { PlansAndPrices } from "./billing/plans-prices";
import { BillingTab } from "./client/tab-billing";

const plan = (code: string, name: string, fee: number, max: number, extra = {}) => ({
  id: `p-${code}`, code, name, monthly_fee_paise: fee, max_active_subscribers: max,
  features: { voice_ai: false, public_api: false, staff_app: false }, is_active: true, is_placeholder: true,
  updated_at: "2026-10-29T06:00:00Z", ...extra,
});
const PLANS = [
  plan("starter", "Starter (placeholder)", 199_900, 1000),
  plan("growth", "Growth (placeholder)", 499_900, 5000, { features: { voice_ai: false, public_api: true, staff_app: true } }),
];
const PRICES = {
  current: [{ channel: "whatsapp_utility", price_paise: 85 }, { channel: "whatsapp_service", price_paise: 35 }, { channel: "voice_minute", price_paise: 150 }],
  entries: [
    { id: "e1", channel: "whatsapp_utility", price_paise: 85, effective_from: "2026-04-01", created_at: "" },
    { id: "e2", channel: "whatsapp_utility", price_paise: 90, effective_from: "2099-01-01", created_at: "" },
  ],
};
const billing = (overrides = {}) => ({
  subscription: { plan: PLANS[0], pending_plan: null, status: "active", current_period_start: "2026-10-29", next_billing_date: "2026-11-29" },
  wallet: { balance_paise: 640_235, low_balance_level_paise: 100_000, is_low: false, can_send: true, paused_at: null },
  mandate: null,
  prices: [
    { channel: "whatsapp_utility", price_paise: 60, source: "override" },
    { channel: "whatsapp_service", price_paise: 35, source: "price_list" },
    { channel: "voice_minute", price_paise: 150, source: "price_list" },
  ],
  active_subscribers: 588,
  recent_transactions: [],
  invoices: [],
  ...overrides,
});

describe("Plans and prices", () => {
  it("edits a plan and schedules a price", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", body: authState() },
      { method: "GET", path: "/api/admin/plans", body: PLANS },
      { method: "GET", path: "/api/admin/price-list", body: PRICES },
      { method: "GET", path: "/api/auth/csrf", status: 204 },
      { method: "PATCH", path: "/api/admin/plans/p-starter", body: plan("starter", "Starter", 149_900, 1000, { is_placeholder: false }) },
      { method: "POST", path: "/api/admin/price-list", status: 201, body: PRICES.entries[1] },
    ]);
    renderWithQuery(<PlansAndPrices />);
    const row = await waitFor(() => {
      const found = document.querySelector("tr[data-plan=starter]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(row).toHaveTextContent("Placeholder");
    expect(document.querySelector("tr[data-plan=growth]")).toHaveTextContent("Public API, Delivery staff app");
    await userEvent.click(await within(row).findByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit Starter (placeholder)" });
    expect(dialog).toHaveTextContent("Code: starter (can't be changed)");
    await userEvent.clear(within(dialog).getByLabelText(/^Name/));
    await userEvent.type(within(dialog).getByLabelText(/^Name/), "Starter");
    await userEvent.clear(within(dialog).getByLabelText(/Monthly fee/));
    await userEvent.type(within(dialog).getByLabelText(/Monthly fee/), "1499");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Plan saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ name: "Starter", monthly_fee_paise: 149_900, max_active_subscribers: 1000 });

    expect(document.querySelector("tr[data-entry=e2]")).toHaveTextContent("Scheduled");
    expect(within(document.querySelector("tr[data-entry=e1]") as HTMLElement).queryByRole("button")).not.toBeInTheDocument();
    const prices = screen.getByRole("region", { name: "Price list" });
    await userEvent.type(within(prices).getByLabelText("Price (₹)"), "0.90");
    await userEvent.clear(within(prices).getByLabelText("From"));
    await userEvent.type(within(prices).getByLabelText("From"), "2099-01-01");
    await userEvent.click(within(prices).getByRole("button", { name: "Schedule price" }));
    expect(await within(prices).findByText("Price scheduled.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/admin/price-list")?.body).toEqual({ channel: "whatsapp_utility", price_paise: 90, effective_from: "2099-01-01" });
  });

  it("support staff see plans and prices without controls", async () => {
    mockApi([
      { method: "GET", path: "/api/auth/me", body: authState({ platform_role: "support" }) },
      { method: "GET", path: "/api/admin/plans", body: PLANS },
      { method: "GET", path: "/api/admin/price-list", body: PRICES },
    ]);
    renderWithQuery(<PlansAndPrices />);
    expect(await screen.findByText("Growth (placeholder)")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Add plan" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Schedule price" })).not.toBeInTheDocument();
  });
});

describe("Client Billing tab", () => {
  it("schedules a plan change for the next billing date", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", body: authState() },
      { method: "GET", path: "/api/admin/tenants/t1/subscription", body: billing() },
      { method: "GET", path: "/api/admin/tenants/t1/price-overrides", body: [] },
      { method: "GET", path: "/api/admin/plans", body: PLANS },
      { method: "GET", path: "/api/auth/csrf", status: 204 },
      { method: "POST", path: "/api/admin/tenants/t1/subscription/plan-change", body: billing({ subscription: { ...billing().subscription, pending_plan: PLANS[1] } }) },
    ]);
    renderWithQuery(<BillingTab tenantId="t1" />);
    const card = await screen.findByRole("region", { name: "Plan" });
    expect(card).toHaveTextContent("next billing date 29 Nov 2026");
    expect(screen.getByTestId("admin-wallet-balance")).toHaveTextContent("₹6,402.35");
    expect(document.querySelector("[data-channel=whatsapp_utility]")).toHaveTextContent("Client override");
    await userEvent.selectOptions(await within(card).findByLabelText("Change plan to"), "growth");
    await userEvent.click(within(card).getByRole("button", { name: "Schedule change" }));
    const dialog = await screen.findByRole("dialog", { name: "Change to Growth (placeholder)?" });
    expect(dialog).toHaveTextContent("applies on the next billing date, 29 Nov 2026");
    await userEvent.click(within(dialog).getByRole("button", { name: "Schedule change" }));
    expect(await within(card).findByText("Plan change scheduled for 29 Nov 2026.")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("plan-change"))?.body).toEqual({ plan_code: "growth" });
  });

  it("adds a price override and shows the server's overlap error", async () => {
    const calls = mockApi([
      { method: "GET", path: "/api/auth/me", body: authState() },
      { method: "GET", path: "/api/admin/tenants/t1/subscription", body: billing() },
      {
        method: "GET", path: "/api/admin/tenants/t1/price-overrides",
        body: [{ id: "o1", channel: "whatsapp_utility", price_paise: 60, starts_on: "2026-10-29", ends_on: null, state: "active", created_at: "" }],
      },
      { method: "GET", path: "/api/admin/plans", body: PLANS },
      { method: "GET", path: "/api/auth/csrf", status: 204 },
      { method: "POST", path: "/api/admin/tenants/t1/price-overrides", status: 409, body: { code: "override_overlap", detail: "Overlap", fields: {} } },
    ]);
    renderWithQuery(<BillingTab tenantId="t1" />);
    const group = await screen.findByRole("group", { name: "Price overrides" });
    const row = await waitFor(() => {
      const found = group.querySelector("tr[data-override=whatsapp_utility]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(row).toHaveTextContent("open-ended");
    expect(await within(row).findByRole("button", { name: "End today" })).toBeInTheDocument();
    await userEvent.type(within(group).getByLabelText("Price (₹)"), "0.55");
    await userEvent.click(within(group).getByRole("button", { name: "Add override" }));
    expect(await within(group).findByText("This client already has an override for this channel on these dates.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ channel: "whatsapp_utility", price_paise: 55, ends_on: null });
  });
});

import path from "node:path";

import { expect, test } from "@playwright/test";

import { AUTH_DIR, createClient, manage, unique } from "./support";

const asSuperAdmin = { storageState: path.join(AUTH_DIR, "super_admin.json") };
const asSupport = { storageState: path.join(AUTH_DIR, "support.json") };

/** Step A2: plans, price list, overrides and plan changes (admin spec 2.11, Workflow 3.8). */
test.describe("Billing (super admin)", () => {
  test.use(asSuperAdmin);

  test("a plan change scheduled today applies on the next billing date, with an invoice", async ({ page }) => {
    await page.goto("/clients");
    const client = await createClient(page, `E2E Billing ${unique()}`);
    await page.goto(`/clients/${client.id}?tab=billing`);
    const card = page.getByRole("region", { name: "Plan" });
    await expect(card).toHaveAttribute("data-plan", "starter");
    await card.getByLabel("Change plan to").selectOption("growth");
    await card.getByRole("button", { name: "Schedule change" }).click();
    await page.getByRole("dialog", { name: /Change to Growth/ }).getByRole("button", { name: "Schedule change" }).click();
    await expect(card.locator("[data-pending=growth]")).toBeVisible();
    await expect(card).toHaveAttribute("data-plan", "starter"); // not yet

    // The scheduled task, run as if it were the billing date.
    const out = manage(
      "shell",
      [
        "from apps.billing.plans import apply_billing_date",
        "from apps.billing.payment_services import subscription_for",
        "from apps.core.models import Tenant",
        `t = Tenant.objects.get(pk="${client.id}")`,
        "print('RESULT', apply_billing_date(t), apply_billing_date(t, today=subscription_for(t).next_billing_date))",
      ].join("\n"),
    );
    expect(out).toContain("RESULT not_due plan_changed");
    await page.reload();
    await expect(card).toHaveAttribute("data-plan", "growth");
    await expect(card.locator("[data-pending]")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Wallet" })).toContainText("Subscription");
  });

  test("price overrides: no overlaps, end today; usage then uses the override", async ({ page }) => {
    await page.goto("/clients");
    const client = await createClient(page, `E2E Overrides ${unique()}`);
    await page.goto(`/clients/${client.id}?tab=billing`);
    const group = page.getByRole("group", { name: "Price overrides" });
    await group.getByLabel("Channel").selectOption("voice_minute");
    await group.getByLabel("Price (₹)").fill("1.20");
    await group.getByRole("button", { name: "Add override" }).click();
    await expect(group.getByText("Override added.")).toBeVisible();
    await expect(page.locator("[data-channel=voice_minute]")).toHaveAttribute("data-source", "override");
    await expect(page.locator("[data-channel=voice_minute]")).toContainText("₹1.20");

    await group.getByLabel("Price (₹)").fill("1.10");
    await group.getByRole("button", { name: "Add override" }).click();
    await expect(group.getByText("This client already has an override for this channel on these dates.")).toBeVisible();

    await group.locator("tr[data-override=voice_minute]").getByRole("button", { name: "End today" }).click();
    await expect(group.getByText("Override ends today.")).toBeVisible();
  });

  test("plans and price list", async ({ page }) => {
    await page.goto("/billing");
    const code = `e2e-${unique()}`.slice(0, 30);
    await page.getByRole("button", { name: "Add plan" }).click();
    const add = page.getByRole("dialog", { name: "Add plan" });
    await add.getByLabel(/^Code/).fill(code);
    await add.getByLabel(/^Name/).fill("E2E Plan");
    await add.getByLabel(/Monthly fee/).fill("2999");
    await add.getByLabel(/Max active subscribers/).fill("3000");
    await add.getByLabel("Public API").check();
    await add.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Plan added.")).toBeVisible();
    const row = page.locator(`tr[data-plan="${code}"]`);
    await expect(row).toContainText("₹2,999.00");
    await row.getByRole("button", { name: "Edit" }).click();
    const edit = page.getByRole("dialog", { name: "Edit E2E Plan" });
    await edit.getByLabel(/Active \(clients/).uncheck();
    await edit.getByRole("button", { name: "Save changes" }).click();
    await expect(row).toContainText("Retired");

    const prices = page.getByRole("region", { name: "Price list" });
    const day = `2099-${String(1 + Math.floor(Math.random() * 12)).padStart(2, "0")}-${String(1 + Math.floor(Math.random() * 28)).padStart(2, "0")}`;
    await prices.getByLabel("Channel").selectOption("whatsapp_service");
    await prices.getByLabel("Price (₹)").fill("0.40");
    await prices.getByLabel("From").fill(day);
    await prices.getByRole("button", { name: "Schedule price" }).click();
    await expect(prices.getByText("Price scheduled.")).toBeVisible();
    const scheduled = prices.locator("tr[data-entry]").filter({ hasText: "₹0.40" }).filter({ hasText: "Scheduled" }).first();
    await scheduled.getByRole("button", { name: "Delete" }).click();
    await expect(prices.getByText("Scheduled price withdrawn.")).toBeVisible();
  });
});

test.describe("Billing (support)", () => {
  test.use(asSupport);

  test("support staff see billing without changing it", async ({ page }) => {
    await page.goto("/billing");
    await expect(page.getByRole("region", { name: "Plans" })).toContainText("Growth");
    await expect(page.getByRole("button", { name: "Add plan" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Schedule price" })).toHaveCount(0);
  });
});

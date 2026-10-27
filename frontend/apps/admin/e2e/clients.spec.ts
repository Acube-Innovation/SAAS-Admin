import path from "node:path";

import { expect, test } from "@playwright/test";

import { AUTH_DIR, createClient, unique } from "./support";

const asSuperAdmin = { storageState: path.join(AUTH_DIR, "super_admin.json") };
const asSupport = { storageState: path.join(AUTH_DIR, "support.json") };

test.describe("Workflow 3.1: onboard a client", () => {
  test.use(asSuperAdmin);

  test("super admin creates a client; the owner is invited", async ({ page }) => {
    const id = unique();
    const name = `E2E Agency ${id}`;
    await page.goto("/clients");
    await page.getByRole("button", { name: "New client" }).click();
    await expect(page).toHaveURL(/\/clients\/new$/);

    await page.getByLabel(/^Display name/).fill(name);
    await page.getByLabel(/^Legal name/).fill(`${name} LLP`);
    await page.getByLabel(/^GSTIN/).fill("29abcde1234f1z5");
    await page.getByLabel(/^Address line 1/).fill("Chinnakada Road");
    await page.getByLabel(/^City/).fill("Kollam");
    await page.getByLabel(/^State/).selectOption("KL");
    await page.getByLabel(/^PIN code/).fill("691001");
    await page.getByLabel(/^Contact name/).fill("Suresh");
    await page.getByLabel(/^Contact mobile/).fill("98470 12345");
    await page.getByLabel(/^Contact email/).fill(`office-${id}@e2e.test`);
    await page.getByLabel(/^Owner name/).fill("Suresh Pillai");
    await page.getByLabel(/^Owner email/).fill(`owner-${id}@e2e.test`);

    // A Karnataka GSTIN for a Kerala client is refused before anything is sent.
    await page.getByRole("button", { name: "Create client and invite owner" }).click();
    await expect(page.getByText("This GSTIN does not belong to the selected state.")).toBeVisible();
    await page.getByLabel(/^GSTIN/).fill("");

    await page.getByRole("button", { name: "Create client and invite owner" }).click();
    await expect(page).toHaveURL(/\/clients\/[0-9a-f-]+\?tab=users/);
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByText(`An invitation was emailed to owner-${id}@e2e.test`)).toBeVisible();

    const owner = page.getByRole("row", { name: new RegExp(`owner-${id}@e2e.test`) });
    await expect(owner.getByText("Owner", { exact: true })).toBeVisible();
    await expect(owner.getByText("Invited", { exact: true })).toBeVisible();
    await owner.getByRole("button", { name: "Resend invite" }).click();
    await expect(page.getByText(`A new invitation was sent to owner-${id}@e2e.test`)).toBeVisible();

    await page.getByRole("tab", { name: "Activity" }).click();
    await expect(page.getByRole("cell", { name: "core.tenant" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "accounts.invite" }).first()).toBeVisible();

    await page.goto(`/clients?search=${encodeURIComponent(id)}`);
    await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();
  });
});

test.describe("Workflow 3.2: suspend and reactivate", () => {
  test.use(asSuperAdmin);

  test("suspending needs a reason, shows the read-only banner, and activating clears it", async ({ page }) => {
    await page.goto("/clients");
    const client = await createClient(page, `E2E Suspend ${unique()}`);
    await page.goto(`/clients/${client.id}`);

    await page.getByRole("button", { name: "Suspend" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel(/Reason/).fill("late");
    await dialog.getByRole("button", { name: "Suspend" }).click();
    await expect(dialog.getByText("Give a reason of at least 5 characters.")).toBeVisible();
    await dialog.getByLabel(/Reason/).fill("Payment overdue since 1 October");
    await dialog.getByRole("button", { name: "Suspend" }).click();

    await expect(page.getByText("Client suspended.")).toBeVisible();
    await expect(page.getByText(/Reason: Payment overdue since 1 October\. The client's users have read-only access/)).toBeVisible();
    await expect(page.getByText("Suspended", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Activate" }).click();
    await page.getByRole("dialog").getByLabel(/Reason/).fill("Payment received");
    await page.getByRole("dialog").getByRole("button", { name: "Activate" }).click();
    await expect(page.getByText("Client activated.")).toBeVisible();
    await expect(page.getByText(/read-only access/)).toHaveCount(0);
    await expect(page.getByText("Active", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "Activity" }).click();
    await expect(page.getByRole("cell", { name: "status_change" })).toHaveCount(2);
  });
});

test.describe("Workflow 3.3: support access", () => {
  test("support staff open a client as support, see the banner on every page, and end it", async ({ browser }) => {
    const admin = await browser.newPage(asSuperAdmin);
    await admin.goto("/clients");
    const client = await createClient(admin, `E2E Support ${unique()}`);
    await admin.close();

    const context = await browser.newContext(asSupport);
    const page = await context.newPage();
    await page.goto(`/clients/${client.id}`);
    await expect(page.getByRole("button", { name: "Suspend" })).toHaveCount(0); // super admin only

    await page.getByRole("button", { name: "Open as support" }).click();
    await page.getByRole("dialog").getByLabel(/Reason/).fill("Owner can't see the delivery list");
    await page.getByRole("dialog").getByRole("button", { name: "Open as support" }).click();

    const banner = page.getByText(`Support mode: viewing ${client.name}, read-only.`);
    await expect(banner).toBeVisible();
    await expect(page.getByText(/Time left (59|60):\d\d/)).toBeVisible();

    await page.goto("/dashboard");
    await expect(banner).toBeVisible(); // follows the user across the console

    await page.getByRole("button", { name: "End session" }).click();
    await expect(banner).toHaveCount(0);
    await page.reload();
    await expect(page.getByText(/Support mode/)).toHaveCount(0);

    await page.goto(`/clients/${client.id}?tab=activity`);
    await expect(page.getByRole("cell", { name: /support_start/ })).toBeVisible();
    await expect(page.getByRole("cell", { name: /support_end/ })).toBeVisible();
    await context.close();
  });
});

import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { AUTH_DIR, createClient, unique } from "./support";

const asSuperAdmin = { storageState: path.join(AUTH_DIR, "super_admin.json") };
const asSupport = { storageState: path.join(AUTH_DIR, "support.json") };
const BODY = "Hello {{customer_name}}, your {{cylinder_type}} cylinder is due on {{scheduled_date}}. Will you be home?";

/** A new WhatsApp template (v1 draft) for a fresh client, created through the editor UI. */
async function newTemplate(page: Page) {
  await page.goto("/clients");
  const client = await createClient(page, `E2E Templates ${unique()}`);
  await page.goto("/whatsapp-templates/new");
  await page.getByLabel("Find a client").fill(client.name);
  await page.getByLabel(/^Client/).selectOption({ label: client.name });
  await page.getByLabel(/^Template name/).fill("delivery_reminder");
  await expect(page.getByLabel(/^Schedule type/)).toHaveValue(/.+/);
  await page.getByLabel("Body").fill(BODY);
  await page.getByLabel(/Sample values.*customer_name/).fill("Asha");
  await page.getByLabel(/Sample values.*cylinder_type/).fill("14.2 kg domestic");
  await page.getByLabel(/Sample values.*scheduled_date/).fill("28 Oct 2026");
  await page.getByRole("button", { name: "Create draft" }).click();
  await expect(page).toHaveURL(/\/whatsapp-templates\/[0-9a-f-]+$/);
  await expectStep(page, "Draft");
  return client;
}

async function expectStep(page: Page, label: string) {
  const steps = page.getByRole("navigation", { name: "Approval status" });
  await expect(steps.locator("li[aria-current=step]")).toContainText(label);
}

const simulate = (page: Page, name: string) =>
  page.getByRole("region", { name: "Development only: simulate" }).getByRole("button", { name }).click();

async function act(page: Page, name: string) {
  await page.getByRole("button", { name, exact: true }).click();
}

async function signOffWithEvidence(page: Page) {
  await act(page, "Record client sign-off");
  const dialog = page.getByRole("dialog", { name: "Record the client's sign-off" });
  await dialog.locator("input[type=file]").setInputFiles(path.join(path.dirname(new URL(import.meta.url).pathname), "signoff.pdf"));
  await dialog.getByLabel("Note (optional)").fill("Approved by email");
  await dialog.getByRole("button", { name: "Record sign-off" }).click();
  await expectStep(page, "Signed off");
}

test.describe("Workflow 3.4: WhatsApp template paths (stub provider)", () => {
  test.use(asSuperAdmin);

  test("editor: counters, variables picker and preview", async ({ page }) => {
    await newTemplate(page);
    const body = page.getByLabel("Body");
    await expect(page.getByText(`${BODY.length}/1024`)).toBeVisible();
    await body.click();
    await body.press("End");
    await page.getByRole("button", { name: "{{booking_reference}}" }).click();
    await expect(body).toHaveValue(`${BODY}{{booking_reference}}`);
    await expect(page.getByLabel(/Sample values.*booking_reference/)).toBeVisible();
    const preview = page.getByRole("figure", { name: "Preview" });
    await expect(preview.getByText("Asha")).toBeVisible();
    await expect(preview.getByText("{{booking_reference}}")).toBeVisible();
    await page.getByLabel("Footer (optional)").fill("x".repeat(61));
    await expect(page.getByText("61/60")).toBeVisible();
  });

  test("client send-back -> edit -> client approves -> submit -> Meta approves -> live -> new version", async ({ page }) => {
    await newTemplate(page);
    await act(page, "Send to client for approval");
    await expectStep(page, "Sent to client");

    await page.getByRole("region", { name: "Development only: simulate" }).getByLabel("Comment / reason").fill("Add our phone number");
    await simulate(page, "Client sends back");
    await expect(page.getByText("The client sent this version back")).toBeVisible();
    await expectStep(page, "Sent back");

    await page.getByLabel("Footer (optional)").fill("Call 0484 200 1000. Reply STOP to opt out.");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft saved.")).toBeVisible();
    await expectStep(page, "Draft");

    await act(page, "Send to client for approval");
    await simulate(page, "Client approves");
    await expectStep(page, "Signed off");

    await act(page, "Submit to Meta");
    await expectStep(page, "Submitted to Meta");
    await expect(page.getByText(/Meta template ID: stub-/)).toBeVisible();

    await simulate(page, "Meta approves");
    await expectStep(page, "Approved");
    await act(page, "Make live");
    await expectStep(page, "Live");
    await expect(page.getByText("Approved content")).toHaveCount(0);
    await expect(page.getByLabel("Body")).toBeDisabled();

    await act(page, "New version");
    await expectStep(page, "Draft");
    await expect(page.getByRole("link", { name: /v2/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /v1.*Live/ })).toBeVisible();
  });

  test("manual sign-off with evidence -> submit -> Meta rejects -> new version", async ({ page }) => {
    await newTemplate(page);
    await act(page, "Send to client for approval");
    await signOffWithEvidence(page);
    await expect(page.getByRole("button", { name: "Sign-off evidence" })).toBeVisible();

    await act(page, "Submit to Meta");
    await page.getByRole("region", { name: "Development only: simulate" }).getByLabel("Comment / reason").fill("Promotional wording");
    await simulate(page, "Meta rejects");
    const rejection = page.getByRole("alert").filter({ hasText: "Meta rejected this version" });
    await expect(rejection).toContainText("Promotional wording");

    await act(page, "New version");
    await expectStep(page, "Draft");
  });

  test("live -> Meta pauses -> re-enabled; then disable", async ({ page }) => {
    await newTemplate(page);
    await act(page, "Send to client for approval");
    await signOffWithEvidence(page);
    await act(page, "Submit to Meta");
    await simulate(page, "Meta approves");
    await act(page, "Make live");
    await expectStep(page, "Live");

    await simulate(page, "Meta pauses");
    await expect(page.getByText("Meta paused this template. Sending with it has stopped.")).toBeVisible();
    await expectStep(page, "Paused");
    await simulate(page, "Meta approves");
    await expectStep(page, "Live");

    await act(page, "Disable");
    const dialog = page.getByRole("dialog", { name: "Disable this version?" });
    await dialog.getByLabel(/Reason/).fill("Replaced by a new wording");
    await dialog.getByRole("button", { name: "Disable" }).click();
    await expect(page.getByText("This version is disabled.")).toBeVisible();
  });

  test("support staff draft and send, but can't sign off, submit or publish", async ({ browser }) => {
    const admin = await browser.newPage(asSuperAdmin);
    await newTemplate(admin);
    const url = admin.url();
    await admin.close();

    const page = await (await browser.newContext(asSupport)).newPage();
    await page.goto(url);
    await expectStep(page, "Draft");
    await act(page, "Send to client for approval");
    await expectStep(page, "Sent to client");
    await expect(page.getByRole("button", { name: "Record client sign-off" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Disable" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Development only: simulate" })).toHaveCount(0);
  });
});

test.describe("Industry template editor (spec 2.6)", () => {
  test.use(asSuperAdmin);

  test("saving creates a version; new fields must be optional while clients use it", async ({ page }) => {
    await page.goto("/industry-templates");
    await page.getByRole("link", { name: "Gas agency" }).click();
    await expect(page.getByText(/client\(s\) use this template/)).toBeVisible();
    const version = Number((await page.getByText(/^gas_agency · v\d+$/).textContent())!.split("v")[1]);

    const fields = page.getByRole("table", { name: "Cylinder delivery: Fields" });
    await page.getByRole("button", { name: "Add field" }).nth(1).click();
    const keyInputs = fields.locator("input[aria-label^='Cylinder delivery: Fields: Key']");
    const field = `e2e_${unique()}`.slice(0, 30);
    await keyInputs.last().fill(field);
    await page.getByLabel(`Label: ${field}`).fill("E2E note");
    await expect(page.getByLabel(`Required: ${field}`)).toBeDisabled();

    await page.getByRole("button", { name: "Save as new version" }).click();
    await expect(page.getByText(`Saved as version ${version + 1}.`)).toBeVisible();
    await page.getByRole("tab", { name: "Versions" }).click();
    await expect(page.getByRole("cell", { name: `Version ${version + 1}` })).toBeVisible();

    // Clean up: switch the field off again (it is kept, not deleted).
    await page.getByRole("tab", { name: "Editor" }).click();
    await page.locator(`tr[data-field='${field}']`).getByRole("button", { name: "Switch off" }).click();
    await page.getByRole("button", { name: "Save as new version" }).click();
    await expect(page.getByText(`Saved as version ${version + 2}.`)).toBeVisible();
  });
});

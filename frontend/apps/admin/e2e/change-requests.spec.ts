import path from "node:path";

import { type APIRequestContext, expect, type Page, request, test } from "@playwright/test";

import { AUTH_DIR, manage, PASSWORD, unique } from "./support";

const asSuperAdmin = { storageState: path.join(AUTH_DIR, "super_admin.json") };
const asSupport = { storageState: path.join(AUTH_DIR, "support.json") };

/**
 * Workflow 3.6 (admin side of portal Workflow 3.3). Staff work in the console (this browser); the
 * client's manager acts through the portal API. A seeded demo agency has the reminder template
 * live in English with version 2 already sent to the client.
 */
let agency = "";
let client: APIRequestContext | null = null;

/** The seeded agency's manager, signed in through the portal API (created on first use). */
async function clientSession(baseURL: string) {
  if (client) return client;
  agency = `E2E Requests ${unique()}`;
  manage(`seed_demo --name "${agency}" --subscribers 20 --email-domain e2e.test --password-stdin`, `${PASSWORD}\n`);
  // Empty storage: inside a test, newContext() would inherit the staff session, and logging in on
  // top of it ends that session (Django flushes a session that belonged to another user).
  client = await request.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  await client.get("/api/auth/csrf");
  const email = `manager@${agency.toLowerCase().replaceAll(" ", "-")}.e2e.test`;
  await clientCall("post", "/api/auth/login", { email, password: PASSWORD });
  return client;
}

async function clientCall(method: "get" | "post", url: string, data?: unknown) {
  const csrf = (await client!.storageState()).cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const response = await client![method](url, { data, headers: { "X-CSRFToken": csrf } });
  if (!response.ok()) throw new Error(`${method.toUpperCase()} ${url} -> ${response.status()} ${await response.text()}`);
  return response.json();
}

test.afterAll(async () => {
  await client?.dispose();
});

async function raise(baseURL: string, subject: string) {
  await clientSession(baseURL);
  return clientCall("post", "/api/portal/change-requests", {
    request_type: "edit_template",
    subject,
    description: "Customers keep asking where to call. Please add 0484 2345678.",
  });
}

async function openFromQueue(page: Page, subject: string) {
  await page.goto("/change-requests");
  await page.getByLabel("Search").fill(subject);
  await page.getByLabel("Search").press("Enter");
  await page.getByRole("link", { name: new RegExp(subject) }).click();
  await expect(page.getByRole("heading", { name: subject })).toBeVisible();
}

test.describe("Change requests queue (Workflow 3.6)", () => {
  test.use(asSuperAdmin);

  test("start work -> reply -> link version -> client approves -> submit -> live -> done", async ({ page, baseURL }) => {
    const subject = `Add office number ${unique()}`;
    const raised = await raise(baseURL!, subject);

    await page.goto("/dashboard");
    await page.getByRole("link", { name: /Open change requests/ }).click();
    await expect(page).toHaveURL(/\/change-requests$/);
    await openFromQueue(page, subject);
    await expect(page.getByText(agency, { exact: false }).first()).toBeVisible();

    await page.getByRole("button", { name: "Start work" }).click();
    await expect(page.getByText("Started. The client sees it as in progress.")).toBeVisible();
    await expect(page.getByLabel("Assignee")).not.toHaveValue("");

    await page.getByRole("textbox", { name: /Reply to the client/ }).fill("We'll add it to the footer of the English message.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(page.locator("li[data-from=staff]")).toContainText("We'll add it to the footer");

    const linked = page.getByRole("region", { name: "Linked template version" });
    const select = linked.getByLabel("Version to link");
    const value = await select.locator("option", { hasText: "cylinder_delivery_reminder · EN · v2" }).getAttribute("value");
    await select.selectOption(value!);
    await linked.getByRole("button", { name: "Link version" }).click();
    await expect(page.getByText("Version linked.")).toBeVisible();
    await expect(linked.locator("[data-linked-version='2']")).toContainText("Sent to client");

    // The client sees the reply and approves version 2 in the portal.
    const detail = await clientCall("get", `/api/portal/change-requests/${raised.id}`);
    expect(detail.status).toBe("in_progress");
    expect(detail.comments[0]).toMatchObject({ from_staff: true, body: "We'll add it to the footer of the English message." });
    const { template_id: templateId, id: versionId } = detail.linked_version;
    await clientCall("post", `/api/portal/content/templates/${templateId}/versions/${versionId}/approve`);

    // Submit, Meta approves (stub), make live, from the template editor.
    await linked.getByRole("link", { name: "Open in the template editor" }).click();
    await expect(page).toHaveURL(new RegExp(`/whatsapp-templates/${templateId}\\?v=${versionId}`));
    const steps = page.getByRole("navigation", { name: "Approval status" });
    await expect(steps.locator("li[aria-current=step]")).toContainText("Signed off");
    await page.getByRole("button", { name: "Submit to Meta", exact: true }).click();
    await expect(steps.locator("li[aria-current=step]")).toContainText("Submitted to Meta");
    await page.getByRole("region", { name: "Development only: simulate" }).getByRole("button", { name: "Meta approves" }).click();
    await page.getByRole("button", { name: "Make live", exact: true }).click();
    await expect(steps.locator("li[aria-current=step]")).toContainText("Live");

    // The request closed itself.
    await page.goto(`/change-requests/${raised.id}`);
    await expect(page.getByText("Done", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Send reply" })).toHaveCount(0);
    const live = await clientCall("get", `/api/portal/content/templates/${templateId}`);
    expect(live.live.version).toBe(2);
    expect((await clientCall("get", `/api/portal/change-requests/${raised.id}`)).status).toBe("done");
  });
});

test.describe("Support staff on change requests", () => {
  test.use(asSupport);

  test("support declines with a reason the client sees", async ({ page, baseURL }) => {
    const subject = `Promotional offer text ${unique()}`;
    const raised = await raise(baseURL!, subject);
    await openFromQueue(page, subject);

    await page.getByRole("button", { name: "Decline" }).click();
    const dialog = page.getByRole("dialog", { name: "Decline this request?" });
    await dialog.getByLabel(/Reason/).fill("Meta doesn't allow promotions in utility templates.");
    await dialog.getByRole("button", { name: "Decline" }).click();
    await expect(page.getByText("Declined.")).toBeVisible();
    await expect(page.getByText("Meta doesn't allow promotions in utility templates.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Decline" })).toHaveCount(0);

    const seen = await clientCall("get", `/api/portal/change-requests/${raised.id}`);
    expect(seen).toMatchObject({ status: "declined", decline_reason: "Meta doesn't allow promotions in utility templates." });
  });
});

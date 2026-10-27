import { readFile } from "node:fs/promises";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { apiPost, AUTH_DIR, createClient, unique } from "./support";

test.use({ storageState: path.join(AUTH_DIR, "super_admin.json") });

/** Minimal RFC 4180 parser: quoted cells may hold commas, quotes ("") and new lines. */
function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const endCell = () => {
    row.push(cell);
    cell = "";
  };
  const endRow = () => {
    endCell();
    rows.push(row);
    row = [];
  };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") endCell();
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      endRow();
    } else cell += ch;
  }
  if (cell || row.length) endRow();
  const [header, ...body] = rows;
  return body.map((cells) => Object.fromEntries(header!.map((key, n) => [key, cells[n] ?? ""])));
}

async function suspend(page: Page, id: string) {
  await apiPost(page, `/api/admin/tenants/${id}/suspend`, { reason: "E2E: unpaid invoice" });
}

/** The table's rows as [actor, client, action, record type], as shown on screen. */
async function screenRows(page: Page) {
  const rows = page.getByRole("table", { name: "Audit log" }).locator("tbody tr");
  return rows.evaluateAll((trs) =>
    trs.map((tr) => {
      const cells = [...tr.querySelectorAll("td")].map((td) => td.textContent?.trim() ?? "");
      return [cells[1], cells[2], cells[3]?.replace("Support session", "").trim(), cells[4]?.split(" · ")[0]];
    }),
  );
}

test("dashboard tiles: suspending a client moves it between the client tiles", async ({ page }) => {
  await page.goto("/dashboard");
  const tile = (label: string) => page.getByTestId("tile").and(page.locator(`[data-label="${label}"]`));
  await expect(tile("Active clients")).toContainText(/\d/);
  const read = async (label: string) => Number((await tile(label).locator("dd").first().textContent())!.replace(/,/g, ""));
  const before = { active: await read("Active clients"), suspended: await read("Suspended clients") };
  for (const label of ["Sent", "Delivered", "Failed", "Replied"]) await expect(tile(label)).toContainText("0");
  await expect(tile("WhatsApp number quality")).toContainText(/High|Medium|Low|Unavailable/);

  const client = await createClient(page, `E2E Dash ${unique()}`);
  await suspend(page, client.id);
  await page.reload();

  await expect(tile("Suspended clients").locator("dd").first()).toHaveText(
    (before.suspended + 1).toLocaleString("en-IN"),
  );
  await expect(tile("Active clients").locator("dd").first()).toHaveText(before.active.toLocaleString("en-IN"));
  await tile("Suspended clients").getByRole("link").click();
  await expect(page).toHaveURL(/\/clients\?status=suspended$/);
});

test("audit log: filters, before/after, and a CSV export that matches the screen", async ({ page }) => {
  const name = `E2E Audit ${unique()}`;
  const client = await createClient(page, name);
  await suspend(page, client.id);

  await page.goto("/audit-log");
  await page.getByLabel("Client").selectOption({ label: name });
  await expect(page).toHaveURL(new RegExp(`tenant=${client.id}`));
  const table = page.getByRole("table", { name: "Audit log" });
  // Wait for the filtered rows (the previous page stays on screen while it loads).
  await expect
    .poll(async () => (await screenRows(page)).every((row) => row[1] === name))
    .toBe(true);
  const clientRows = await screenRows(page);
  expect(clientRows.map((row) => row[2])).toContain("status_change");

  // CSV export with the same filters has exactly the on-screen rows, in the same order.
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^audit-log-\d{8}-\d{4}\.csv$/);
  const csv = parseCsv(await readFile((await download.path())!, "utf8"));
  expect(csv.map((r) => [r.actor_email || "System", r.tenant, r.action, r.object_type])).toEqual(clientRows);
  await expect(page.getByRole("status").filter({ hasText: "Exported" })).toContainText(
    `Exported ${clientRows.length} entries`,
  );

  // Narrow to the suspension and read the before/after comparison.
  await page.getByLabel("Action").selectOption("status_change");
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await table.getByRole("button", { name: "View" }).click();
  const dialog = page.getByRole("dialog", { name: "Audit entry" });
  const status = dialog.getByRole("row", { name: /^status/ });
  await expect(status).toHaveAttribute("data-changed", "true");
  await expect(status).toContainText("active");
  await expect(status).toContainText("suspended");
  await page.keyboard.press("Escape");

  // The export itself is in the audit log.
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/audit-log$/);
  await page.getByLabel("Action").selectOption("export");
  await expect(table.locator("tbody tr").first()).toContainText("audit.auditlog");
  await expect(table.locator("tbody tr").first()).toContainText("e2e-super-admin");
});

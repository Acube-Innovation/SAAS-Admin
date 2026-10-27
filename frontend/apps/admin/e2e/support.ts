import { execSync } from "node:child_process";
import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { APIRequestContext, Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const AUTH_DIR = path.join(HERE, ".auth");
export const PASSWORD = "e2e-passphrase-2026";

/** Run a Django management command: in docker compose locally, directly in CI (E2E_MANAGE). */
export function manage(args: string, input?: string): string {
  const command = process.env.E2E_MANAGE ?? "docker compose exec -T api python manage.py";
  const cwd = process.env.E2E_MANAGE_CWD ?? path.resolve(HERE, "../../../..");
  return execSync(`${command} ${args}`, { cwd, input, encoding: "utf8" });
}

/** RFC 6238 TOTP for a hex key (django-otp's TOTPDevice.key). */
export function totp(hexKey: string, offset = 0): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000) + offset));
  const hmac = crypto.createHmac("sha1", Buffer.from(hexKey, "hex")).update(counter).digest();
  const at = hmac[hmac.length - 1]! & 0xf;
  return String((hmac.readUInt32BE(at) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

async function csrfHeader(request: APIRequestContext) {
  const state = await request.storageState();
  const token = state.cookies.find((cookie) => cookie.name === "csrftoken")?.value ?? "";
  return { "X-CSRFToken": token };
}

/** Call the API through the admin app's /api proxy with the page's session. */
export async function apiPost(page: Page, url: string, data: unknown) {
  const response = await page.request.post(url, { data, headers: await csrfHeader(page.request) });
  if (!response.ok()) throw new Error(`${url} -> ${response.status()} ${await response.text()}`);
  return response.json();
}

/** Create a client through the API (for tests that start from an existing client). */
export async function createClient(page: Page, name: string) {
  const templates = await (await page.request.get("/api/admin/industry-templates")).json();
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "");
  return apiPost(page, "/api/admin/tenants", {
    industry_template: templates[0].id,
    name,
    legal_name: `${name} LLP`,
    address_line1: "Main Road",
    city: "Kochi",
    state: "KL",
    pin_code: "682016",
    contact_name: "Office",
    contact_phone: "9847000001",
    contact_email: `office@${slug}.test`,
    owner_name: "Owner",
    owner_email: `owner@${slug}.test`,
  });
}

export const unique = () => `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

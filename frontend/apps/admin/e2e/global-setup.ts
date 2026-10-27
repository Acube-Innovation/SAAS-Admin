import fs from "node:fs";
import path from "node:path";

import { type FullConfig, request } from "@playwright/test";

import { AUTH_DIR, manage, PASSWORD, totp, unique } from "./support";

/**
 * Create a fresh super admin and support user for this run, sign each in with real 2FA (TOTP from
 * the set-up secret) through the admin app's API proxy, and save the signed-in sessions.
 */
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0]!.use.baseURL!;
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  const run = unique();

  for (const role of ["super_admin", "support"] as const) {
    const email = `e2e-${role.replace("_", "-")}-${run}@e2e.test`;
    manage(`createplatformuser --email ${email} --full-name "E2E ${role}" --role ${role} --password-stdin`, `${PASSWORD}\n`);

    const api = await request.newContext({ baseURL });
    await api.get("/api/auth/csrf");
    const csrf = async () => ({
      "X-CSRFToken": (await api.storageState()).cookies.find((c) => c.name === "csrftoken")?.value ?? "",
    });
    let response = await api.post("/api/auth/login", { data: { email, password: PASSWORD }, headers: await csrf() });
    if (!response.ok()) throw new Error(`login ${email}: ${response.status()} ${await response.text()}`);
    const setup = await (await api.post("/api/auth/otp/setup", { headers: await csrf() })).json();
    response = await api.post("/api/auth/otp/confirm", { data: { otp_code: totp(setup.secret) }, headers: await csrf() });
    if (!response.ok()) throw new Error(`2FA ${email}: ${response.status()} ${await response.text()}`);

    await api.storageState({ path: path.join(AUTH_DIR, `${role}.json`) });
    fs.writeFileSync(path.join(AUTH_DIR, `${role}.email`), email);
    await api.dispose();
  }
}

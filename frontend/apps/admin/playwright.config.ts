import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running API (docker compose locally, services in CI).
 * E2E_BASE_URL: the admin app; E2E_CHROME_PATH: use an installed Chrome instead of Playwright's.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: ci ? 1 : 0,
  timeout: 60_000,
  reporter: ci ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: process.env.E2E_CHROME_PATH ? { executablePath: process.env.E2E_CHROME_PATH } : {},
  },
  webServer: {
    command: ci ? "pnpm start" : "pnpm dev",
    url: `${baseURL}/login`,
    reuseExistingServer: !ci,
    timeout: 120_000,
  },
});

export { ApiError, createApiClient, csrfMiddleware, readCookie, unwrap } from "./client";
export type { ApiClient, ApiErrorBody } from "./client";
export type { components, paths } from "./schema";

/** Health check used by the scaffold pages and monitoring. */
export type Health = {
  status: "ok" | "error";
  database: "ok" | "error";
  redis: "ok" | "error";
};

export async function getHealth(baseUrl: string, fetchImpl: typeof fetch = fetch): Promise<Health> {
  const response = await fetchImpl(`${baseUrl.replace(/\/$/, "")}/api/health`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  // 503 still carries a Health body describing which dependency is down.
  if (!response.ok && response.status !== 503) {
    throw new Error(`Health check failed with HTTP ${response.status}`);
  }
  return (await response.json()) as Health;
}

export type AuthState = import("./schema").components["schemas"]["AuthState"];

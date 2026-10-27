/**
 * Typed API client. In Step 8 the types here are generated from the OpenAPI schema at /api/schema;
 * for now it only covers the health endpoint.
 */
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

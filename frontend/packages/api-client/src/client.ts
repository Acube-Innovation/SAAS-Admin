import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/** The backend's error body (docs/admin-console-spec.md Section 7). */
export type ApiErrorBody = {
  code: string;
  detail: string;
  fields: Record<string, string[] | Record<string, unknown>>;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: ApiErrorBody["fields"];

  constructor(status: number, body: Partial<ApiErrorBody> | undefined) {
    super(body?.detail ?? `Request failed with HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "error";
    this.fields = body?.fields ?? {};
  }
}

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function readCookie(name: string, cookieString?: string): string | undefined {
  const source = cookieString ?? (typeof document === "undefined" ? "" : document.cookie);
  for (const part of source.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

/** Adds Django's CSRF token (read fresh from the cookie: it rotates at sign-in) to unsafe requests. */
export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (UNSAFE_METHODS.has(request.method)) {
      const token = readCookie("csrftoken");
      if (token) request.headers.set("X-CSRFToken", token);
    }
    return request;
  },
};

/**
 * Typed client for the Django API. Apps call it same-origin (`baseUrl: ""`): Next.js rewrites
 * /api/* to the backend, so the session and CSRF cookies belong to the app's own origin.
 */
export function createApiClient(options: { baseUrl?: string; fetch?: typeof fetch } = {}) {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? "",
    credentials: "include",
    fetch: options.fetch,
  });
  client.use(csrfMiddleware);
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Unwrap an openapi-fetch result: return data, or throw ApiError with the backend's body. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new ApiError(response.status, error as Partial<ApiErrorBody>);
  return data as T;
}

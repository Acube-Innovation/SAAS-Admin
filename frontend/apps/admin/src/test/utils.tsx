import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";

export const router = { replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), back: vi.fn(), prefetch: vi.fn() };
export const navigation = { pathname: "/dashboard", search: new URLSearchParams() };

export function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

type Handler = { method: string; path: string; status?: number; body?: unknown };

/** Replace fetch with canned API responses; returns the list of requests made. */
export function mockApi(handlers: Handler[]) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      const request = typeof input === "string" ? new Request(new URL(input, "http://localhost:3000"), init) : input;
      const url = new URL(request.url);
      const text = request.method === "GET" ? "" : await request.text();
      let body: unknown = undefined;
      if (text) {
        try {
          body = JSON.parse(text);
        } catch {
          body = text; // e.g. multipart form data
        }
      }
      calls.push({ method: request.method, path: url.pathname, body });
      const handler = handlers.find((h) => h.method === request.method && h.path === url.pathname);
      const status = handler?.status ?? (handler ? 200 : 404);
      return new Response(handler?.body === undefined ? null : JSON.stringify(handler.body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

export function authState(overrides: Record<string, unknown> = {}) {
  return {
    id: "u1",
    email: "admin@acube.test",
    full_name: "Platform Admin",
    platform_role: "super_admin",
    requires_2fa: true,
    otp_device_confirmed: true,
    otp_verified: true,
    otp_required: false,
    otp_setup_required: false,
    active_tenant_id: null,
    memberships: [],
    ...overrides,
  };
}

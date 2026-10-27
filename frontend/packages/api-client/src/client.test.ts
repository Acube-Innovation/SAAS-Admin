import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient, readCookie, unwrap } from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("readCookie", () => {
  it("finds a cookie by name", () => {
    expect(readCookie("csrftoken", "sessionid=abc; csrftoken=t0k%3Den")).toBe("t0k=en");
    expect(readCookie("missing", "a=1")).toBeUndefined();
  });
});

describe("createApiClient", () => {
  it("sends the CSRF token on unsafe requests only, with credentials", async () => {
    vi.stubGlobal("document", { cookie: "csrftoken=abc123" });
    const fetchImpl = vi.fn(async (request: Request) => {
      void request;
      return jsonResponse({});
    });
    const api = createApiClient({ baseUrl: "http://test", fetch: fetchImpl as typeof fetch });

    await api.POST("/api/auth/logout");
    await api.GET("/api/auth/me");

    const [post, get] = fetchImpl.mock.calls.map(([request]) => request as Request);
    expect(post?.headers.get("X-CSRFToken")).toBe("abc123");
    expect(post?.credentials).toBe("include");
    expect(get?.headers.get("X-CSRFToken")).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("unwrap", () => {
  it("returns data on success", async () => {
    const api = createApiClient({
      baseUrl: "http://test",
      fetch: (async () => jsonResponse({ status: "ok" })) as typeof fetch,
    });
    await expect(unwrap(api.GET("/api/health"))).resolves.toEqual({ status: "ok" });
  });

  it("throws ApiError with the backend's code, detail and fields", async () => {
    const body = { code: "invalid_credentials", detail: "Invalid email or password.", fields: {} };
    const api = createApiClient({
      baseUrl: "http://test",
      fetch: (async () => jsonResponse(body, 400)) as typeof fetch,
    });

    const error = await unwrap(api.POST("/api/auth/login", { body: { email: "a@b.c", password: "x" } }))
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, code: "invalid_credentials", message: body.detail });
  });
});

// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { config, proxy } from "./proxy";

function request(path: string, cookie?: string) {
  return new NextRequest(new URL(path, "http://localhost:3000"), {
    headers: cookie ? { cookie } : {},
  });
}

describe("proxy", () => {
  it.each(["/dashboard", "/clients", "/audit-log?page=2"])(
    "redirects signed-out visitors from %s to /login with ?next",
    (path) => {
      const response = proxy(request(path));
      expect(response.status).toBe(307);
      const location = new URL(response.headers.get("location") ?? "");
      expect(location.pathname).toBe("/login");
      expect(location.searchParams.get("next")).toBe(path);
    },
  );

  it.each(["/login", "/two-factor", "/invite/abc.def"])("lets anyone open %s", (path) => {
    expect(proxy(request(path)).headers.get("location")).toBeNull();
  });

  it("lets requests with a session through (the console then checks it with the API)", () => {
    expect(proxy(request("/clients", "sessionid=abc")).headers.get("location")).toBeNull();
  });

  it("does not run on the API proxy or static files", () => {
    const pattern = new RegExp(`^${config.matcher[0]}$`);
    expect(pattern.test("/api/auth/me")).toBe(false);
    expect(pattern.test("/_next/static/chunk.js")).toBe(false);
    expect(pattern.test("/dashboard")).toBe(true);
  });
});

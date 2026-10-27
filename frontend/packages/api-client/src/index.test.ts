import { describe, expect, it, vi } from "vitest";

import { getHealth } from "./index";

function jsonResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("getHealth", () => {
  it("calls /api/health and returns the body", async () => {
    const body = { status: "ok", database: "ok", redis: "ok" };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body, 200));

    await expect(getHealth("http://api:8000/", fetchImpl)).resolves.toEqual(body);
    expect(fetchImpl).toHaveBeenCalledWith("http://api:8000/api/health", expect.any(Object));
  });

  it("returns the body of a 503 so callers can show what is down", async () => {
    const body = { status: "error", database: "ok", redis: "error" };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body, 503));

    await expect(getHealth("http://api:8000", fetchImpl)).resolves.toEqual(body);
  });

  it("throws on other errors", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("nope", { status: 500 }));

    await expect(getHealth("http://api:8000", fetchImpl)).rejects.toThrow("HTTP 500");
  });
});

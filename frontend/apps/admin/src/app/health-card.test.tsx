import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { HealthCard } from "./health-card";

describe("HealthCard", () => {
  it("shows every dependency as healthy", () => {
    const html = renderToStaticMarkup(
      <HealthCard health={{ status: "ok", database: "ok", redis: "ok" }} apiUrl="http://api" />,
    );
    expect(html).toContain("PostgreSQL");
    expect(html).toContain("Redis");
    expect(html).not.toContain("Unavailable");
  });

  it("flags a dependency that is down", () => {
    const html = renderToStaticMarkup(
      <HealthCard health={{ status: "error", database: "ok", redis: "error" }} apiUrl="http://api" />,
    );
    expect(html).toContain("Unavailable");
  });

  it("says when the API cannot be reached", () => {
    const html = renderToStaticMarkup(<HealthCard health={null} apiUrl="http://api" />);
    expect(html).toContain("API not reachable at http://api");
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusPill } from "./status-pill";

describe("StatusPill", () => {
  it("renders its label with the tone colour", () => {
    const html = renderToStaticMarkup(<StatusPill tone="ok">Healthy</StatusPill>);
    expect(html).toContain("Healthy");
    expect(html).toContain("text-emerald-700");
  });
});

import { describe, expect, it } from "vitest";

import { renderWithSamples, usedVariables, variablesIn, withKnownSamples } from "./content";

describe("template content helpers", () => {
  it("finds variables and ignores malformed ones", () => {
    expect(variablesIn("Hi {{customer_name}}, {{ agency_name }} {{1}} {{Bad}}")).toEqual(["customer_name", "agency_name"]);
  });

  it("lists header and body variables once, in order", () => {
    expect(usedVariables({ header_text: "{{agency_name}}", body: "{{customer_name}} {{agency_name}}" })).toEqual([
      "agency_name",
      "customer_name",
    ]);
  });

  it("replaces variables with samples and marks missing ones", () => {
    expect(renderWithSamples("Hi {{customer_name}} on {{scheduled_date}}.", { customer_name: "Asha" })).toEqual([
      { text: "Hi " },
      { text: "Asha", variable: "customer_name", missing: false },
      { text: " on " },
      { text: "{{scheduled_date}}", variable: "scheduled_date", missing: true },
      { text: "." },
    ]);
  });
});

describe("withKnownSamples", () => {
  const base = { header_text: "", footer: "", buttons: [] };

  it("fills the agency name when the body uses it and the sample is empty", () => {
    const content = { ...base, body: "From {{agency_name}}", sample_values: {} };
    expect(withKnownSamples(content, "Kochi Gas").sample_values).toEqual({ agency_name: "Kochi Gas" });
  });

  it("keeps a sample the user typed and adds nothing unused", () => {
    expect(withKnownSamples({ ...base, body: "From {{agency_name}}", sample_values: { agency_name: "KG" } }, "Kochi Gas").sample_values).toEqual({ agency_name: "KG" });
    expect(withKnownSamples({ ...base, body: "Hi", sample_values: {} }, "Kochi Gas").sample_values).toEqual({});
  });
});

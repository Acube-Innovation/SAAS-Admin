import { describe, expect, it } from "vitest";

import { createTranslator } from "./i18n";

describe("createTranslator", () => {
  const t = createTranslator({ greeting: "Hello {name}", plain: "Plain text" });

  it("returns the message for a key", () => {
    expect(t("plain")).toBe("Plain text");
  });

  it("fills named parameters", () => {
    expect(t("greeting", { name: "Asha" })).toBe("Hello Asha");
  });

  it("falls back to the key when a message is missing", () => {
    expect(t("missing.key")).toBe("missing.key");
  });
});

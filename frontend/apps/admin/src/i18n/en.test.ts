import { describe, expect, it } from "vitest";

import { t } from ".";
import { en } from "./en";

describe("English strings", () => {
  it.each(["super_admin", "support", "owner", "manager", "office_staff"])("has a label for role %s", (role) => {
    expect(t(`role.${role}`)).not.toBe(`role.${role}`);
  });

  it("covers every validation message the shared schemas use", () => {
    for (const key of [
      "validation.required",
      "validation.email",
      "validation.otp",
      "validation.password_min",
      "validation.password_match",
      "validation.reason_min",
      "validation.reason_max",
      "validation.mobile",
      "validation.gstin",
      "validation.pin",
    ]) {
      expect(en[key], key).toBeTruthy();
    }
  });
});

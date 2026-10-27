import { describe, expect, it } from "vitest";

import { authState } from "@/test/utils";

import { nextRoute, safeNext, setupKey } from "./auth";

describe("nextRoute", () => {
  it("sends signed-out users to sign in", () => {
    expect(nextRoute(null)).toBe("/login");
  });

  it("sends users who still need 2FA to the two-factor screen", () => {
    expect(nextRoute(authState({ otp_setup_required: true, otp_verified: false }) as never)).toBe(
      "/two-factor",
    );
    expect(nextRoute(authState({ otp_required: true, otp_verified: false }) as never)).toBe("/two-factor");
  });

  it("refuses users who are not platform staff", () => {
    expect(nextRoute(authState({ platform_role: "" }) as never)).toBe("denied");
  });

  it("lets verified platform staff in", () => {
    expect(nextRoute(authState() as never)).toBeNull();
    expect(nextRoute(authState({ platform_role: "support" }) as never)).toBeNull();
  });
});

describe("safeNext", () => {
  it.each([
    ["/clients", "/clients"],
    [null, "/dashboard"],
    ["https://evil.example", "/dashboard"],
    ["//evil.example", "/dashboard"],
    ["/login", "/dashboard"],
  ])("%s -> %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });
});

describe("setupKey", () => {
  it("extracts and groups the base32 secret", () => {
    expect(setupKey("otpauth://totp/SC:admin?secret=JBSWY3DPEHPK3PXP&issuer=SC")).toBe("JBSW Y3DP EHPK 3PXP");
  });
});

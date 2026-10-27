import { describe, expect, it } from "vitest";

import {
  gstinSchema,
  loginSchema,
  mobileSchema,
  newPasswordSchema,
  normalizeIndianMobile,
  otpSchema,
  pinSchema,
  reasonSchema,
} from "./schemas";

const messages = (result: { success: boolean; error?: { issues: { message: string }[] } }) =>
  result.success ? [] : (result.error?.issues.map((i) => i.message) ?? []);

describe("spec Section 2 validation", () => {
  it("login needs an email and a password", () => {
    expect(messages(loginSchema.safeParse({ email: "x", password: "" }))).toEqual([
      "validation.email",
      "validation.required",
    ]);
  });

  it("OTP codes are exactly 6 digits", () => {
    expect(otpSchema.safeParse({ otp_code: "123456" }).success).toBe(true);
    expect(messages(otpSchema.safeParse({ otp_code: "12345a" }))).toEqual(["validation.otp"]);
  });

  it("new passwords need 12 characters and must match", () => {
    expect(
      messages(newPasswordSchema.safeParse({ password: "short", password_confirm: "short" })),
    ).toEqual(["validation.password_min"]);
    expect(
      messages(
        newPasswordSchema.safeParse({ password: "long-enough-pass", password_confirm: "different-pass" }),
      ),
    ).toEqual(["validation.password_match"]);
  });

  it("reasons are 5 to 500 characters", () => {
    expect(messages(reasonSchema.safeParse({ reason: " bad " }))).toEqual(["validation.reason_min"]);
    expect(messages(reasonSchema.safeParse({ reason: "x".repeat(501) }))).toEqual([
      "validation.reason_max",
    ]);
    expect(reasonSchema.safeParse({ reason: "Unpaid invoice" }).success).toBe(true);
  });

  it.each([
    ["9847012345", "+919847012345"],
    ["+91 98470-12345", "+919847012345"],
    ["919847012345", "+919847012345"],
    ["09847012345", "+919847012345"],
  ])("normalises mobile %s", (raw, expected) => {
    expect(normalizeIndianMobile(raw)).toBe(expected);
    expect(mobileSchema.parse(raw)).toBe(expected);
  });

  it("rejects invalid mobiles", () => {
    expect(messages(mobileSchema.safeParse("5847012345"))).toEqual(["validation.mobile"]);
  });

  it("GSTIN is uppercased, optional, and checked", () => {
    expect(gstinSchema.parse("32abcde1234f1z5")).toBe("32ABCDE1234F1Z5");
    expect(gstinSchema.parse("")).toBe("");
    expect(messages(gstinSchema.safeParse("32ABCDE1234F1Y5"))).toEqual(["validation.gstin"]);
  });

  it("PIN codes are 6 digits not starting with 0", () => {
    expect(pinSchema.safeParse("682001").success).toBe(true);
    expect(messages(pinSchema.safeParse("082001"))).toEqual(["validation.pin"]);
  });
});

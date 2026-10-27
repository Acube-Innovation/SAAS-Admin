/**
 * Client-side validation matching docs/admin-console-spec.md Section 2. The server applies the
 * same rules; these give immediate feedback in forms. Messages are translation keys.
 */
import { z } from "zod";

export const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
export const PIN_RE = /^[1-9][0-9]{5}$/;

export const loginSchema = z.object({
  email: z.email("validation.email"),
  password: z.string().min(1, "validation.required"),
});

export const otpSchema = z.object({
  otp_code: z.string().regex(/^\d{6}$/, "validation.otp"),
});

/** Spec 2.1: minimum 12 characters (the server also rejects common and all-numeric passwords). */
export const newPasswordSchema = z
  .object({
    full_name: z.string().max(100, "validation.max100").optional(),
    password: z.string().min(12, "validation.password_min"),
    password_confirm: z.string(),
  })
  .refine((data) => data.password === data.password_confirm, {
    path: ["password_confirm"],
    message: "validation.password_match",
  });

/** Suspend / activate / support access: a reason of 5-500 characters (Workflows 3.2, 3.3). */
export const reasonSchema = z.object({
  reason: z.string().trim().min(5, "validation.reason_min").max(500, "validation.reason_max"),
});

/** Indian mobile: returns +91 and 10 digits, or throws (same rule as the backend). */
export function normalizeIndianMobile(value: string): string {
  let digits = value.replace(/[\s\-()]/g, "");
  if (digits.startsWith("+91")) digits = digits.slice(3);
  else if (digits.startsWith("91") && digits.length === 12) digits = digits.slice(2);
  else if (digits.startsWith("0") && digits.length === 11) digits = digits.slice(1);
  if (!/^[6-9][0-9]{9}$/.test(digits)) throw new Error("validation.mobile");
  return `+91${digits}`;
}

export const mobileSchema = z.string().transform((value, ctx) => {
  try {
    return normalizeIndianMobile(value);
  } catch {
    ctx.addIssue({ code: "custom", message: "validation.mobile" });
    return z.NEVER;
  }
});

export const gstinSchema = z
  .string()
  .trim()
  .transform((value) => value.toUpperCase())
  .refine((value) => value === "" || GSTIN_RE.test(value), "validation.gstin");

export const pinSchema = z.string().trim().regex(PIN_RE, "validation.pin");

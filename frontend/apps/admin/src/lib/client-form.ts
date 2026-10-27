/**
 * The create-client form (spec 2.4) and the details form (spec 2.5), validated with the same rules
 * as the API. Messages are translation keys.
 */
import { gstinSchema, mobileSchema, pinSchema } from "@saas/api-client/schemas";
import { z } from "zod";

const text = (max: number, min = 1) =>
  z
    .string()
    .trim()
    .min(min, min > 1 ? "validation.min2" : "validation.required")
    .max(max, max === 200 ? "validation.max200" : "validation.max100");

const optionalMobile = z
  .string()
  .trim()
  .transform((value, ctx) => {
    if (!value) return "";
    const parsed = mobileSchema.safeParse(value);
    if (!parsed.success) {
      ctx.addIssue({ code: "custom", message: "validation.mobile" });
      return z.NEVER;
    }
    return parsed.data;
  });

export const detailsFields = {
  name: text(100, 2),
  legal_name: text(200, 2),
  gstin: gstinSchema,
  address_line1: text(200),
  address_line2: z.string().trim().max(200, "validation.max200"),
  city: text(100),
  state: z.string().min(1, "validation.required"),
  pin_code: pinSchema,
  contact_name: text(100),
  contact_phone: mobileSchema,
  contact_email: z.email("validation.email"),
};

export const settingsFields = {
  default_language: z.enum(["ml", "en", "hi", "ta"]),
  daily_send_cap: z.coerce
    .number<string | number>("validation.send_cap")
    .int("validation.send_cap")
    .min(1, "validation.send_cap")
    .max(100_000, "validation.send_cap"),
};

const gstinAndState = z.object({ gstin: gstinSchema, state: z.string().min(1) });

/**
 * GSTIN's first two digits are the GST code of its state. Runs as soon as those two fields are
 * valid, even while other fields still have errors, so every problem shows at once.
 */
export function withGstinStateCheck<T extends z.ZodType<{ gstin: string; state: string }>>(
  schema: T,
  gstCodes: Record<string, string>,
) {
  return schema.refine(
    (data) => !data.gstin || !data.state || gstCodes[data.state] === data.gstin.slice(0, 2),
    {
      path: ["gstin"],
      message: "validation.gstin_state",
      when: (payload) => gstinAndState.safeParse(payload.value).success,
    },
  );
}

export function createClientSchema(gstCodes: Record<string, string>) {
  return withGstinStateCheck(
    z.object({
      industry_template: z.string().min(1, "validation.required"),
      ...detailsFields,
      ...settingsFields,
      owner_name: text(100),
      owner_email: z.email("validation.email"),
      owner_phone: optionalMobile,
    }),
    gstCodes,
  );
}

export function detailsSchema(gstCodes: Record<string, string>) {
  return withGstinStateCheck(z.object(detailsFields), gstCodes);
}

export const settingsSchema = z.object({ ...settingsFields, timezone: z.string().min(1) });

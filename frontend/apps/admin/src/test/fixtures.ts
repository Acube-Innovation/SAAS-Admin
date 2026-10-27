export const REFERENCE = {
  states: [
    { code: "KL", name: "Kerala", gst_code: "32" },
    { code: "KA", name: "Karnataka", gst_code: "29" },
  ],
  languages: [
    { code: "ml", name: "Malayalam" },
    { code: "en", name: "English" },
  ],
  timezones: ["Asia/Kolkata"],
  daily_send_cap: { default: 5000, min: 1, max: 100000 },
};

export const INDUSTRIES = [{ id: "tpl-gas", key: "gas_agency", name: "Gas agency", is_active: true }];

export function tenant(overrides: Record<string, unknown> = {}) {
  return {
    id: "t1",
    name: "Kochi Gas Agency",
    legal_name: "Kochi Gas Agency Pvt Ltd",
    gstin: "32ABCDE1234F1Z5",
    address_line1: "MG Road",
    address_line2: "",
    city: "Kochi",
    state: "KL",
    pin_code: "682016",
    contact_name: "Asha",
    contact_phone: "+919847000001",
    contact_email: "office@kochigas.test",
    default_language: "ml",
    timezone: "Asia/Kolkata",
    daily_send_cap: 5000,
    industry_template: "tpl-gas",
    industry_template_name: "Gas agency",
    industry_template_version: 1,
    industry_settings: {
      schedule_types: {
        cylinder_delivery: {
          reminder_rule: { send_time: "10:00", days_before: 1, wait_minutes: 240, quiet_start: "21:00", quiet_end: "08:00", delivery_weekdays: [0, 1, 2, 3, 4, 5] },
          question_flow: { answers: [{ label: "Yes, available", outcome: "confirmed" }] },
        },
      },
      choice_options: { "cylinder_delivery.cylinder_type": ["14.2 kg domestic", "5 kg"] },
    },
    status: "active",
    suspended_at: null,
    suspension_reason: "",
    owner_email: "asha@kochigas.test",
    created_at: "2026-10-27T10:00:00Z",
    updated_at: "2026-10-27T10:00:00Z",
    ...overrides,
  };
}

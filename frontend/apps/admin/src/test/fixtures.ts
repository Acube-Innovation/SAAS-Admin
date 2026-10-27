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

export function waVersion(overrides: Record<string, unknown> = {}) {
  return {
    id: "v1",
    version: 1,
    status: "draft",
    is_live: false,
    header_text: "",
    body: "Hello {{customer_name}}, delivery on {{scheduled_date}}.",
    footer: "Reply STOP to stop these messages.",
    buttons: [
      { text: "Yes, available", outcome: "confirmed" },
      { text: "Not available", outcome: "not_available" },
    ],
    sample_values: { customer_name: "Asha", scheduled_date: "28 Oct 2026" },
    provider_template_id: "",
    rejection_reason: "",
    has_evidence: false,
    signoff_method: "",
    signed_off_by_email: null,
    signed_off_at: null,
    submitted_at: null,
    approved_at: null,
    created_at: "2026-10-27T10:00:00Z",
    available_actions: ["edit", "send_to_client", "disable"],
    events: [{ action: "create", from_status: "", to_status: "draft", actor_email: "admin@acube.test", note: "", created_at: "2026-10-27T10:00:00Z" }],
    ...overrides,
  };
}

export function waTemplate(versions = [waVersion()]) {
  return {
    id: "wt1",
    tenant_id: "t1",
    tenant_name: "Kochi Gas Agency",
    name: "delivery_reminder",
    language: "en",
    category: "utility",
    schedule_type: "st1",
    schedule_type_name: "Cylinder delivery",
    live_version: null,
    latest_version: { id: versions[0]!.id, version: versions[0]!.version, status: versions[0]!.status },
    created_at: "2026-10-27T10:00:00Z",
    versions,
  };
}

export function industryTemplate(clientsUsing = 3) {
  const field = (key: string, extra: Record<string, unknown> = {}) => ({
    key, label: key, field_type: "text", required: false, is_core: false, options: [], max_length: null, is_active: true, ...extra,
  });
  return {
    id: "it1",
    key: "gas_agency",
    name: "Gas agency",
    description: "",
    is_active: true,
    current_version: 2,
    clients_using: clientsUsing,
    updated_at: "2026-10-27T10:00:00Z",
    subscriber_fields: [field("customer_name", { required: true, is_core: true })],
    schedule_types: [
      {
        id: "st1",
        key: "cylinder_delivery",
        name: "Cylinder delivery",
        is_active: true,
        fields: [field("booking_reference", { required: true, is_core: true }), field("quantity", { field_type: "number", required: true })],
        default_reminder_rule: { send_time: "10:00", days_before: 1, wait_minutes: 240, quiet_start: "21:00", quiet_end: "08:00", delivery_weekdays: [0, 1, 2, 3, 4, 5], channel_order: ["whatsapp"] },
        default_question_flow: { answers: [{ key: "yes", label: "Yes, available", outcome: "confirmed" }] },
      },
    ],
  };
}

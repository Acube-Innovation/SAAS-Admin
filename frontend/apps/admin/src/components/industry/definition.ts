/** The industry template definition as the editor holds it (the PUT body, spec 2.6). */
export type FieldRow = {
  key: string;
  label: string;
  field_type: "text" | "number" | "date" | "phone" | "choice" | "boolean";
  required: boolean;
  is_core: boolean;
  options: string[];
  max_length: number | null;
  is_active: boolean;
  /** Added in this editing session (not saved yet): its key is still editable. */
  isNew?: boolean;
};

export type Rule = {
  send_time: string;
  days_before: number;
  wait_minutes: number;
  quiet_start: string;
  quiet_end: string;
  delivery_weekdays: number[];
  channel_order: string[];
};

export type Answer = { key: string; label: string; outcome: string; pick_next_delivery_days?: number };

export type ScheduleTypeRow = {
  key: string;
  name: string;
  is_active: boolean;
  fields: FieldRow[];
  default_reminder_rule: Rule;
  default_question_flow: { answers: Answer[] };
  isNew?: boolean;
};

export type Definition = {
  key: string;
  name: string;
  description: string;
  is_active: boolean;
  subscriber_fields: FieldRow[];
  schedule_types: ScheduleTypeRow[];
};

export const FIELD_TYPES = ["text", "number", "date", "phone", "choice", "boolean"] as const;
export const OUTCOMES = ["confirmed", "not_available", "reschedule"] as const;
export const BUTTON_MAX = 25;

export const DEFAULT_RULE: Rule = {
  send_time: "10:00",
  days_before: 1,
  wait_minutes: 240,
  quiet_start: "21:00",
  quiet_end: "08:00",
  delivery_weekdays: [0, 1, 2, 3, 4, 5],
  channel_order: ["whatsapp"],
};

export const DEFAULT_FLOW = {
  answers: [
    { key: "yes_available", label: "Yes, available", outcome: "confirmed" },
    { key: "not_available", label: "Not available", outcome: "not_available" },
    { key: "change_date", label: "Change date", outcome: "reschedule", pick_next_delivery_days: 5 },
  ],
};

export function newField(): FieldRow {
  return { key: "", label: "", field_type: "text", required: false, is_core: false, options: [], max_length: null, is_active: true, isNew: true };
}

export function blankDefinition(): Definition {
  return {
    key: "",
    name: "",
    description: "",
    is_active: true,
    subscriber_fields: [],
    schedule_types: [
      {
        key: "",
        name: "",
        is_active: true,
        fields: [],
        default_reminder_rule: { ...DEFAULT_RULE },
        default_question_flow: structuredClone(DEFAULT_FLOW),
        isNew: true,
      },
    ],
  };
}

/** The API body: drop editor-only flags. */
export function toPayload(definition: Definition) {
  const withoutFlag = <T extends { isNew?: boolean }>(item: T): Omit<T, "isNew"> => {
    const copy = { ...item };
    delete copy.isNew;
    return copy;
  };
  return {
    ...definition,
    subscriber_fields: definition.subscriber_fields.map(withoutFlag),
    schedule_types: definition.schedule_types.map((st) => ({ ...withoutFlag(st), fields: st.fields.map(withoutFlag) })),
  };
}

/** From the API's read shape. */
export function fromTemplate(template: {
  key: string;
  name: string;
  description: string;
  is_active: boolean;
  subscriber_fields: unknown;
  schedule_types: unknown;
}): Definition {
  return structuredClone({
    key: template.key,
    name: template.name,
    description: template.description,
    is_active: template.is_active,
    subscriber_fields: template.subscriber_fields as FieldRow[],
    schedule_types: template.schedule_types as ScheduleTypeRow[],
  });
}

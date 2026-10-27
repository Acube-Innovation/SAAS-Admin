import { ApiError } from "@saas/api-client";

import { errorMessage } from "@/i18n";

/**
 * Nested validation errors as readable lines, e.g. "schedule_types › 1 › default_reminder_rule ›
 * send_time: Use HH:MM (24-hour)." (editors whose payload is a whole nested document).
 */
export function errorLines(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== "validation_error") return [errorMessage(error)];
  const lines: string[] = [];
  const walk = (value: unknown, path: string[]) => {
    if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
      lines.push(`${path.join(" › ")}: ${value.join(" ")}`);
    } else if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, [...path, String(index + 1)]));
    } else if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) walk(child, [...path, key]);
    }
  };
  walk(error.fields, []);
  return lines.length ? lines : [errorMessage(error)];
}

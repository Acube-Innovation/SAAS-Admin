import { ApiError } from "@saas/api-client";
import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { errorMessage } from "@/i18n";

/**
 * Put the API's per-field errors on the form fields (same snake_case names in the API and the
 * form). Returns a message for anything that isn't a field error, or undefined.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: readonly string[],
): string | undefined {
  if (error instanceof ApiError && error.code === "validation_error") {
    let unplaced = false;
    for (const [field, messages] of Object.entries(error.fields)) {
      const message = Array.isArray(messages) ? String(messages[0]) : String(messages);
      if (fields.includes(field)) setError(field as Path<T>, { type: "server", message });
      else unplaced = true;
    }
    return unplaced ? errorMessage(error) : undefined;
  }
  return errorMessage(error);
}

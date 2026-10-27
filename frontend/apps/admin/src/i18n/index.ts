import { ApiError } from "@saas/api-client";
import { createTranslator } from "@saas/ui";

import { en } from "./en";

export const t = createTranslator(en);

/** A translated message for an API error: by its code when we have one, else the server's text. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const key = `error.${error.code}`;
    const translated = t(key);
    return translated === key ? error.message || t("error.generic") : translated;
  }
  if (error instanceof TypeError) return t("error.network");
  return t("error.generic");
}

/** Zod messages are translation keys; translate them for display. */
export function fieldError(error?: { message?: string }): string | undefined {
  return error?.message ? t(error.message) : undefined;
}

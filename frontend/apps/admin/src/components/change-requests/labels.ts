import type { StatusTone } from "@saas/ui";

export const REQUEST_TYPES = ["new_template", "edit_template", "new_language", "voice_script", "plan_change", "other"] as const;
export const REQUEST_STATUSES = ["open", "in_progress", "done", "declined"] as const;
export const REQUEST_TONES: Record<string, StatusTone> = {
  open: "warn",
  in_progress: "info",
  done: "ok",
  declined: "neutral",
};

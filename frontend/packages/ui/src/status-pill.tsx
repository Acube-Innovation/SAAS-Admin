import { cn } from "./cn";

export type StatusTone = "ok" | "warn" | "error" | "info" | "neutral";

const toneClasses: Record<StatusTone, string> = {
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  warn: "bg-amber-50 text-amber-800 ring-amber-600/20",
  error: "bg-red-50 text-red-700 ring-red-600/20",
  info: "bg-sky-50 text-sky-700 ring-sky-600/20",
  neutral: "bg-slate-50 text-slate-600 ring-slate-500/20",
};

export function StatusPill({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return (
    <span
      data-tone={tone}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        toneClasses[tone],
      )}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

/** Colour for each status value used by the API (tenants, memberships, templates, sessions). */
export const STATUS_TONES: Record<string, StatusTone> = {
  active: "ok",
  approved: "ok",
  live: "ok",
  signed_off: "info",
  submitted: "info",
  sent_to_client: "warn",
  sent_back: "warn",
  paused: "warn",
  invited: "neutral",
  draft: "neutral",
  suspended: "error",
  rejected: "error",
  disabled: "error",
  deactivated: "error",
};

/** A status pill whose colour follows the status value; `label` is the translated text. */
export function StatusBadge({ status, label }: { status: string; label: string }) {
  return <StatusPill tone={STATUS_TONES[status] ?? "neutral"}>{label}</StatusPill>;
}

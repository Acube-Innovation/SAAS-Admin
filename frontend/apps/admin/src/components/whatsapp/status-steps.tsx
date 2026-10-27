import { cn } from "@saas/ui";

import { t } from "@/i18n";

const STEPS = ["draft", "sent_to_client", "signed_off", "submitted", "approved", "live"] as const;

/** Where a status sits on the happy path, and how to mark that step when it's a detour. */
function position(status: string, isLive: boolean): { index: number; tone?: "warn" | "error"; label?: string } {
  switch (status) {
    case "draft":
      return { index: 0 };
    case "sent_back":
      return { index: 1, tone: "warn", label: t("status.sent_back") };
    case "sent_to_client":
      return { index: 1 };
    case "signed_off":
      return { index: 2 };
    case "submitted":
      return { index: 3 };
    case "rejected":
      return { index: 4, tone: "error", label: t("status.rejected") };
    case "approved":
      return { index: isLive ? 5 : 4 };
    case "paused":
      return { index: isLive ? 5 : 4, tone: "warn", label: t("status.paused") };
    default:
      return { index: -1 };
  }
}

/** Workflow 3.4 status at the top of the editor. */
export function StatusSteps({ status, isLive }: { status: string; isLive: boolean }) {
  const current = position(status, isLive);
  return (
    <nav aria-label={t("wa.status_title")}>
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-3">
        {STEPS.map((step, index) => {
          const done = current.index > index || (index === current.index && step === "live");
          const here = index === current.index;
          const tone = here ? current.tone : undefined;
          return (
            <li key={step} className="flex items-center gap-2" aria-current={here ? "step" : undefined}>
              <span
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold",
                  tone === "error" && "bg-red-600 text-white",
                  tone === "warn" && "bg-amber-500 text-white",
                  !tone && (done ? "bg-brand-600 text-white" : here ? "bg-white text-brand-700 ring-2 ring-brand-600" : "bg-slate-100 text-slate-400"),
                )}
              >
                {done && !tone ? "✓" : index + 1}
              </span>
              <span className={cn("text-sm", here ? "font-semibold text-ink" : done ? "text-ink" : "text-slate-400")}>
                {here && current.label ? current.label : t(`wa.step.${step}`)}
              </span>
              {index < STEPS.length - 1 && <span aria-hidden className="mx-1 h-px w-6 bg-slate-300" />}
            </li>
          );
        })}
      </ol>
      {status === "disabled" && <p className="mt-2 text-sm font-medium text-red-700">{t("wa.disabled")}</p>}
    </nav>
  );
}

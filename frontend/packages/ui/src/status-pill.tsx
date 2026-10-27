export type StatusTone = "ok" | "error" | "neutral";

const toneClasses: Record<StatusTone, string> = {
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  error: "bg-red-50 text-red-700 ring-red-600/20",
  neutral: "bg-slate-50 text-slate-600 ring-slate-500/20",
};

export function StatusPill({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${toneClasses[tone]}`}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

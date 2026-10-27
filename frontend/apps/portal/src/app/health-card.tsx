import type { Health } from "@saas/api-client";
import { StatusPill } from "@saas/ui";

import { t } from "@/i18n";

type Props = { health: Health | null; apiUrl: string };

export function HealthCard({ health, apiUrl }: Props) {
  const rows: { label: string; ok: boolean }[] = health
    ? [
        { label: t("health.api"), ok: true },
        { label: t("health.database"), ok: health.database === "ok" },
        { label: t("health.redis"), ok: health.redis === "ok" },
      ]
    : [];

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-ink">{t("health.title")}</h2>
      <p className="mt-1 font-mono text-xs text-muted">GET {apiUrl}/api/health</p>
      {health ? (
        <ul className="mt-4 divide-y divide-slate-100">
          {rows.map((row) => (
            <li key={row.label} className="flex items-center justify-between py-2.5 text-sm">
              <span>{row.label}</span>
              <StatusPill tone={row.ok ? "ok" : "error"}>
                {row.ok ? t("health.ok") : t("health.error")}
              </StatusPill>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-4">
          <StatusPill tone="error">{t("health.unreachable", { url: apiUrl })}</StatusPill>
        </div>
      )}
    </section>
  );
}

"use client";

import { cn, Dialog, StatusPill } from "@saas/ui";

import { t } from "@/i18n";
import { diffRows } from "@/lib/audit";
import { formatDateTime } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 py-1 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="break-all text-ink">{children}</dd>
    </div>
  );
}

/** One audit entry: who, when, where, and a field-by-field before/after comparison (spec 2.8). */
export function AuditDetail({ entry, onClose }: { entry: AuditEntry | null; onClose: () => void }) {
  const rows = entry ? diffRows(entry.before, entry.after) : [];
  return (
    <Dialog open={entry !== null} title={t("audit.detail.title")} onClose={onClose} size="lg">
      {entry && (
        <div className="space-y-4">
          <dl className="divide-y divide-slate-100">
            <Meta label={t("audit.col.time")}>{formatDateTime(entry.created_at)}</Meta>
            <Meta label={t("audit.col.actor")}>{entry.actor_email || t("activity.system")}</Meta>
            <Meta label={t("audit.col.client")}>{entry.tenant_name ?? t("audit.platform")}</Meta>
            <Meta label={t("audit.col.action")}>
              <span className="font-mono text-xs">{entry.action}</span>
            </Meta>
            <Meta label={t("audit.col.record")}>
              <span className="font-mono text-xs">
                {entry.object_type || t("common.none")}
                {entry.object_id && ` · ${entry.object_id}`}
              </span>
            </Meta>
            {entry.support_session_id && (
              <Meta label={t("audit.support_session")}>
                <StatusPill tone="warn">{t("activity.support")}</StatusPill>{" "}
                <span className="font-mono text-xs">{entry.support_session_id}</span>
              </Meta>
            )}
            <Meta label={t("audit.col.ip")}>{entry.ip_address ?? t("common.none")}</Meta>
            {entry.user_agent && <Meta label={t("audit.user_agent")}>{entry.user_agent}</Meta>}
          </dl>

          <div>
            <div className="mb-2 flex items-center gap-2">
              <h3 className="text-sm font-semibold text-ink">{t("audit.detail.changes")}</h3>
              {rows.length > 0 && (
                <StatusPill tone="info">
                  {t("audit.detail.changed_count", { count: rows.filter((row) => row.changed).length })}
                </StatusPill>
              )}
            </div>
            {rows.length === 0 ? (
              <p className="text-sm text-muted">{t("audit.detail.no_changes")}</p>
            ) : (
              <div className="max-h-96 overflow-auto rounded-lg border border-slate-200">
                <table className="min-w-full text-xs">
                  <caption className="sr-only">{t("audit.detail.changes")}</caption>
                  <thead className="sticky top-0 bg-slate-50 text-left text-muted">
                    <tr>
                      <th scope="col" className="w-40 px-3 py-2 font-medium">{t("audit.detail.field")}</th>
                      <th scope="col" className="px-3 py-2 font-medium">{t("audit.detail.before")}</th>
                      <th scope="col" className="px-3 py-2 font-medium">{t("audit.detail.after")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 font-mono">
                    {rows.map((row) => (
                      <tr key={row.field} data-changed={row.changed} className={cn(row.changed && "bg-amber-50/60")}>
                        <th scope="row" className="px-3 py-2 text-left font-medium text-ink">{row.field}</th>
                        <td className={cn("break-all px-3 py-2", row.changed ? "text-red-700" : "text-muted")}>
                          {row.before || t("common.none")}
                        </td>
                        <td className={cn("break-all px-3 py-2", row.changed ? "text-emerald-700" : "text-muted")}>
                          {row.after || t("common.none")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

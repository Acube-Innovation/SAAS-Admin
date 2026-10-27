"use client";

import { Alert, type Column, DataTable, StatusPill } from "@saas/ui";
import Link from "next/link";
import { useState } from "react";

import { Pagination } from "@/components/pagination";
import { errorMessage, t } from "@/i18n";
import { formatDateTime } from "@/lib/format";
import { useTenantActivity } from "@/lib/queries";
import type { AuditLogEntry } from "@/lib/types";

/** Activity tab: this client's audit entries, newest first (spec 2.5). */
export function ActivityTab({ tenantId }: { tenantId: string }) {
  const [page, setPage] = useState(1);
  const activity = useTenantActivity(tenantId, page);
  const columns: Column<AuditLogEntry>[] = [
    { key: "time", header: t("activity.col.time"), render: (row) => formatDateTime(row.created_at) },
    { key: "actor", header: t("activity.col.actor"), render: (row) => row.actor_email || t("activity.system") },
    {
      key: "action",
      header: t("activity.col.action"),
      render: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs">{row.action}</span>
          {row.support_session_id && <StatusPill tone="warn">{t("activity.support")}</StatusPill>}
        </span>
      ),
    },
    { key: "object", header: t("activity.col.object"), render: (row) => <span className="font-mono text-xs text-muted">{row.object_type}</span> },
  ];
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Link href={`/audit-log?tenant=${tenantId}`} className="text-sm font-medium text-brand-700 hover:underline">
          {t("activity.open_audit_log")}
        </Link>
      </div>
      {activity.isError && <Alert>{errorMessage(activity.error)}</Alert>}
      <DataTable
        caption={t("client.tab.activity")}
        columns={columns}
        rows={activity.data?.results}
        rowKey={(row) => row.id}
        loading={activity.isPending}
        loadingLabel={t("app.loading")}
        empty={t("activity.empty")}
      />
      {activity.data && activity.data.count > 0 && <Pagination page={page} count={activity.data.count} onChange={setPage} />}
    </div>
  );
}

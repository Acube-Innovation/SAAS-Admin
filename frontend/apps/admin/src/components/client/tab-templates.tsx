"use client";

import { Alert, type Column, DataTable, StatusBadge } from "@saas/ui";

import { errorMessage, t } from "@/i18n";
import { useTenantTemplates } from "@/lib/queries";
import type { WhatsAppTemplate } from "@/lib/types";

/** Templates tab: the client's WhatsApp templates and their status (editor in Step 10). */
export function TemplatesTab({ tenantId }: { tenantId: string }) {
  const templates = useTenantTemplates(tenantId);
  const columns: Column<WhatsAppTemplate>[] = [
    { key: "name", header: t("templates.col.name"), render: (row) => <span className="font-mono text-xs">{row.name}</span> },
    { key: "language", header: t("templates.col.language"), render: (row) => row.language.toUpperCase() },
    {
      key: "status",
      header: t("templates.col.status"),
      render: (row) => {
        const latest = row.latest_version as { version: number; status: string };
        return (
          <span className="flex items-center gap-2">
            v{latest.version} <StatusBadge status={latest.status} label={t(`status.${latest.status}`)} />
          </span>
        );
      },
    },
    { key: "live", header: t("templates.col.live"), render: (row) => (row.live_version ? `v${row.live_version}` : t("common.none")) },
  ];
  return (
    <div className="space-y-3">
      {templates.isError && <Alert>{errorMessage(templates.error)}</Alert>}
      <DataTable
        caption={t("client.tab.templates")}
        columns={columns}
        rows={templates.data?.results}
        rowKey={(row) => row.id}
        loading={templates.isPending}
        loadingLabel={t("app.loading")}
        empty={t("templates.empty")}
      />
      <p className="text-xs text-muted">{t("templates.editor_soon")}</p>
    </div>
  );
}

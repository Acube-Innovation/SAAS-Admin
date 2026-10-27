"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, type Column, DataTable, SelectField, StatusPill, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { errorMessage, t } from "@/i18n";
import { api } from "@/lib/api";
import {
  activeFilters,
  AUDIT_PAGE_SIZE,
  type AuditFilters,
  filenameFrom,
  readAuditFilters,
} from "@/lib/audit";
import { formatDateTime } from "@/lib/format";
import { keys, useAuditFacets, useAuditLogs } from "@/lib/queries";
import type { AuditEntry } from "@/lib/types";

import { AuditDetail } from "./audit-detail";

function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Audit log (spec 2.8): filters in the URL, before/after detail, CSV export (itself audited). */
export function AuditLog() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const filters = readAuditFilters(params);
  const page = params.get("page") ?? "1";
  const [actor, setActor] = useState(filters.actor);
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exported, setExported] = useState<number | null>(null);

  const query = activeFilters(filters);
  const entries = useAuditLogs({ ...query, page, page_size: String(AUDIT_PAGE_SIZE) });
  const facets = useAuditFacets();

  function update(changes: Partial<AuditFilters> & { page?: string }) {
    const next = new URLSearchParams({ ...filters, page: "1", ...changes });
    for (const [key, value] of [...next.entries()]) if (!value || (key === "page" && value === "1")) next.delete(key);
    setExported(null);
    router.replace(next.size ? `${pathname}?${next}` : pathname);
  }

  async function exportCsv() {
    setExporting(true);
    setExportError(null);
    setExported(null);
    try {
      const result = api.GET("/api/admin/audit-logs/export", { params: { query }, parseAs: "blob" });
      const { response } = await result;
      const blob = (await unwrap(result)) as unknown as Blob;
      saveFile(blob, filenameFrom(response.headers.get("Content-Disposition")));
      setExported(entries.data?.count ?? 0);
      // The export is itself an audit entry.
      await queryClient.invalidateQueries({ queryKey: keys.auditLogs });
    } catch (error) {
      setExportError(error);
    } finally {
      setExporting(false);
    }
  }

  const columns: Column<AuditEntry>[] = [
    {
      key: "time",
      header: t("audit.col.time"),
      className: "whitespace-nowrap",
      render: (row) => formatDateTime(row.created_at),
    },
    { key: "actor", header: t("audit.col.actor"), render: (row) => row.actor_email || t("activity.system") },
    {
      key: "client",
      header: t("audit.col.client"),
      render: (row) => row.tenant_name ?? <span className="text-muted">{t("audit.platform")}</span>,
    },
    {
      key: "action",
      header: t("audit.col.action"),
      render: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs">{row.action}</span>
          {row.support_session_id && <StatusPill tone="warn">{t("activity.support")}</StatusPill>}
        </span>
      ),
    },
    {
      key: "record",
      header: t("audit.col.record"),
      render: (row) => (
        <span className="font-mono text-xs text-muted" title={row.object_id}>
          {row.object_type || t("common.none")}
          {row.object_id && ` · ${row.object_id.slice(0, 8)}`}
        </span>
      ),
    },
    {
      key: "ip",
      header: t("audit.col.ip"),
      render: (row) => <span className="font-mono text-xs text-muted">{row.ip_address ?? t("common.none")}</span>,
    },
    {
      key: "view",
      header: t("audit.col.details"),
      render: (row) => (
        <Button
          variant="secondary"
          onClick={(event) => {
            event.stopPropagation();
            setSelected(row);
          }}
        >
          {t("audit.view")}
        </Button>
      ),
    },
  ];

  const anyFilter = Object.keys(query).length > 0;
  const options = (values: string[] | undefined) => (values ?? []).map((value) => ({ value, label: value }));
  return (
    <>
      <PageHeader
        title={t("page.audit_log.title")}
        description={t("page.audit_log.description")}
        actions={
          <Button onClick={exportCsv} disabled={exporting || !entries.data?.count}>
            {exporting ? t("audit.exporting") : t("audit.export")}
          </Button>
        }
      />
      <form
        role="search"
        aria-label={t("audit.filters")}
        className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
        onSubmit={(event) => {
          event.preventDefault();
          update({ actor: actor.trim() });
        }}
      >
        <SelectField
          label={t("audit.filter.client")}
          value={filters.tenant}
          placeholder={t("clients.all")}
          options={[
            ...(facets.data?.tenants ?? []).map((tenant) => ({ value: tenant.id, label: tenant.name })),
          ]}
          onChange={(event) => update({ tenant: event.target.value })}
        />
        <TextField
          label={t("audit.filter.actor")}
          type="search"
          placeholder={t("audit.filter.actor_placeholder")}
          value={actor}
          onChange={(event) => setActor(event.target.value)}
          onBlur={() => actor.trim() !== filters.actor && update({ actor: actor.trim() })}
        />
        <SelectField
          label={t("audit.filter.action")}
          value={filters.action}
          placeholder={t("clients.all")}
          options={options(facets.data?.actions)}
          onChange={(event) => update({ action: event.target.value })}
        />
        <SelectField
          label={t("audit.filter.record")}
          value={filters.object_type}
          placeholder={t("clients.all")}
          options={options(facets.data?.object_types)}
          onChange={(event) => update({ object_type: event.target.value })}
        />
        <TextField
          label={t("audit.filter.from")}
          type="date"
          value={filters.date_from}
          max={filters.date_to || undefined}
          onChange={(event) => update({ date_from: event.target.value })}
        />
        <TextField
          label={t("audit.filter.to")}
          type="date"
          value={filters.date_to}
          min={filters.date_from || undefined}
          onChange={(event) => update({ date_to: event.target.value })}
        />
      </form>
      {anyFilter && (
        <div className="mb-4 flex items-center gap-3 text-sm text-muted">
          <span>{t("audit.filtered")}</span>
          <Button
            variant="secondary"
            onClick={() => {
              setActor("");
              router.replace(pathname);
            }}
          >
            {t("audit.clear")}
          </Button>
        </div>
      )}
      <div className="space-y-3">
        {exported !== null && (
          <Alert tone="success">{t("audit.exported", { count: exported })}</Alert>
        )}
        {exportError !== null && <Alert>{errorMessage(exportError)}</Alert>}
        {entries.isError && <Alert>{errorMessage(entries.error)}</Alert>}
        <DataTable
          caption={t("page.audit_log.title")}
          columns={columns}
          rows={entries.data?.results}
          rowKey={(row) => row.id}
          loading={entries.isPending}
          loadingLabel={t("app.loading")}
          empty={anyFilter ? t("audit.empty") : t("audit.empty_all")}
          onRowClick={setSelected}
        />
      </div>
      {entries.data && entries.data.count > 0 && (
        <Pagination
          page={Number(page)}
          count={entries.data.count}
          pageSize={AUDIT_PAGE_SIZE}
          onChange={(next) => update({ page: String(next) })}
        />
      )}
      <AuditDetail entry={selected} onClose={() => setSelected(null)} />
    </>
  );
}

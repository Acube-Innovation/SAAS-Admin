"use client";

import { Alert, type Column, DataTable, SelectField, StatusPill, TextField } from "@saas/ui";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { errorMessage, t } from "@/i18n";
import { formatDateTime } from "@/lib/format";
import { useAssignees, useChangeRequests, useTenants } from "@/lib/queries";
import type { AdminChangeRequest } from "@/lib/types";

import { REQUEST_STATUSES, REQUEST_TONES, REQUEST_TYPES } from "./labels";

const FILTERS = ["status", "tenant", "assignee", "request_type", "search", "page"] as const;

/** Change requests queue (spec 2.9): filters by status, client, assignee and type. */
export function ChangeRequestQueue() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? ""])) as Record<(typeof FILTERS)[number], string>;
  // Open and in-progress requests unless another status is picked.
  const status = params.has("status") ? filters.status : "active";
  const query = Object.fromEntries(Object.entries({ ...filters, status }).filter(([, v]) => v));
  const requests = useChangeRequests(query);
  const tenants = useTenants({ page: "1" });
  const assignees = useAssignees();
  const [search, setSearch] = useState(filters.search);

  function update(changes: Partial<typeof filters>) {
    const next = new URLSearchParams({ ...filters, page: "", ...changes });
    for (const [key, value] of [...next.entries()]) if ((!value && key !== "status") || (key === "page" && value === "1")) next.delete(key);
    if (!params.has("status") && !("status" in changes)) next.delete("status");
    router.replace(next.size ? `${pathname}?${next}` : pathname);
  }

  const columns: Column<AdminChangeRequest>[] = [
    {
      key: "subject",
      header: t("cr.col.subject"),
      render: (row) => (
        <Link href={`/change-requests/${row.id}`} className="block">
          <span className="font-medium text-ink hover:text-brand-700">{row.subject}</span>
          <span className="block text-xs text-muted">{t(`cr.type.${row.request_type}`)}</span>
        </Link>
      ),
    },
    { key: "tenant", header: t("cr.col.client"), render: (row) => row.tenant_name },
    { key: "status", header: t("cr.col.status"), render: (row) => <StatusPill tone={REQUEST_TONES[row.status] ?? "neutral"}>{t(`cr.status.${row.status}`)}</StatusPill> },
    { key: "assignee", header: t("cr.col.assignee"), render: (row) => row.assignee ? (row.assignee.full_name || row.assignee.email) : t("cr.unassigned") },
    { key: "comments", header: t("cr.col.comments"), render: (row) => row.comment_count },
    { key: "updated", header: t("cr.col.updated"), render: (row) => formatDateTime(row.updated_at) },
  ];

  return (
    <>
      <PageHeader title={t("page.change_requests.title")} description={t("page.change_requests.description")} />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_11rem_13rem_12rem_12rem]">
        <form role="search" onSubmit={(e) => { e.preventDefault(); update({ search: search.trim() }); }}>
          <TextField label={t("cr.search")} type="search" value={search} onChange={(e) => setSearch(e.target.value)} onBlur={() => search.trim() !== filters.search && update({ search: search.trim() })} />
        </form>
        <SelectField
          label={t("cr.col.status")}
          value={status}
          placeholder={t("cr.all")}
          options={[{ value: "active", label: t("cr.status.active") }, ...REQUEST_STATUSES.map((s) => ({ value: s, label: t(`cr.status.${s}`) }))]}
          onChange={(e) => update({ status: e.target.value })}
        />
        <SelectField
          label={t("cr.col.client")}
          value={filters.tenant}
          placeholder={t("cr.all")}
          options={(tenants.data?.results ?? []).map((tenant) => ({ value: tenant.id, label: tenant.name }))}
          onChange={(e) => update({ tenant: e.target.value })}
        />
        <SelectField
          label={t("cr.col.assignee")}
          value={filters.assignee}
          placeholder={t("cr.all")}
          options={[
            { value: "me", label: t("cr.assigned_to_me") },
            { value: "none", label: t("cr.unassigned") },
            ...(assignees.data ?? []).map((u) => ({ value: u.id, label: u.full_name || u.email })),
          ]}
          onChange={(e) => update({ assignee: e.target.value })}
        />
        <SelectField
          label={t("cr.type")}
          value={filters.request_type}
          placeholder={t("cr.all")}
          options={REQUEST_TYPES.map((type) => ({ value: type, label: t(`cr.type.${type}`) }))}
          onChange={(e) => update({ request_type: e.target.value })}
        />
      </div>
      {requests.isError && <Alert>{errorMessage(requests.error)}</Alert>}
      <DataTable
        caption={t("page.change_requests.title")}
        columns={columns}
        rows={requests.data?.results}
        rowKey={(row) => row.id}
        loading={requests.isPending}
        loadingLabel={t("app.loading")}
        empty={t("cr.empty")}
        onRowClick={(row) => router.push(`/change-requests/${row.id}`)}
      />
      {requests.data && requests.data.count > 0 && (
        <Pagination page={Number(filters.page || "1")} count={requests.data.count} pageSize={50} onChange={(page) => update({ page: String(page) })} />
      )}
    </>
  );
}

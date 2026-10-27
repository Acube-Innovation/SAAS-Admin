"use client";

import { Alert, Button, type Column, DataTable, SelectField, StatusBadge, TextField } from "@saas/ui";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { errorMessage, t } from "@/i18n";
import { useMe } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useIndustryTemplates, useTenants } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";
import type { Tenant } from "@/lib/types";

/** Clients list (spec 2.3): search, filter by status and industry, 25 per page. */
export function ClientsList() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const me = useMe();
  const industries = useIndustryTemplates();
  const filters = {
    search: params.get("search") ?? "",
    status: params.get("status") ?? "",
    industry_template: params.get("industry_template") ?? "",
    page: params.get("page") ?? "1",
  };
  const [search, setSearch] = useState(filters.search);
  const tenants = useTenants(filters);

  function update(changes: Partial<typeof filters>) {
    const next = new URLSearchParams({ ...filters, page: "1", ...changes });
    for (const [key, value] of [...next.entries()]) if (!value || (key === "page" && value === "1")) next.delete(key);
    router.replace(next.size ? `${pathname}?${next}` : pathname);
  }

  const columns: Column<Tenant>[] = [
    {
      key: "name",
      header: t("clients.col.name"),
      render: (row) => (
        <Link href={`/clients/${row.id}`} className="block">
          <span className="font-medium text-ink hover:text-brand-700">{row.name}</span>
          <span className="block text-xs text-muted">{row.legal_name}</span>
        </Link>
      ),
    },
    { key: "industry", header: t("clients.col.industry"), render: (row) => row.industry_template_name ?? t("common.none") },
    {
      key: "status",
      header: t("clients.col.status"),
      render: (row) => <StatusBadge status={row.status} label={t(`status.${row.status}`)} />,
    },
    { key: "location", header: t("clients.col.location"), render: (row) => `${row.city}, ${row.state}` },
    { key: "owner", header: t("clients.col.owner"), render: (row) => row.owner_email ?? t("common.none") },
    { key: "created", header: t("clients.col.created"), render: (row) => formatDate(row.created_at) },
  ];

  const anyFilter = Boolean(filters.search || filters.status || filters.industry_template);
  return (
    <>
      <PageHeader
        title={t("page.clients.title")}
        description={t("page.clients.description")}
        actions={
          isSuperAdmin(me.data) && (
            <Link href="/clients/new">
              <Button>{t("clients.new")}</Button>
            </Link>
          )
        }
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_12rem_14rem]">
        <form
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            update({ search: search.trim() });
          }}
        >
          <TextField
            label={t("clients.search")}
            type="search"
            placeholder={t("clients.search_placeholder")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onBlur={() => search.trim() !== filters.search && update({ search: search.trim() })}
          />
        </form>
        <SelectField
          label={t("clients.filter_status")}
          value={filters.status}
          placeholder={t("clients.all")}
          options={[
            { value: "active", label: t("status.active") },
            { value: "suspended", label: t("status.suspended") },
          ]}
          onChange={(event) => update({ status: event.target.value })}
        />
        <SelectField
          label={t("clients.filter_industry")}
          value={filters.industry_template}
          placeholder={t("clients.all")}
          options={(industries.data ?? []).map((tpl) => ({ value: tpl.key, label: tpl.name }))}
          onChange={(event) => update({ industry_template: event.target.value })}
        />
      </div>
      {tenants.isError && <Alert>{errorMessage(tenants.error)}</Alert>}
      <DataTable
        caption={t("page.clients.title")}
        columns={columns}
        rows={tenants.data?.results}
        rowKey={(row) => row.id}
        loading={tenants.isPending}
        loadingLabel={t("app.loading")}
        empty={anyFilter ? t("clients.empty") : t("clients.empty_all")}
        onRowClick={(row) => router.push(`/clients/${row.id}`)}
      />
      {tenants.data && tenants.data.count > 0 && (
        <Pagination
          page={Number(filters.page)}
          count={tenants.data.count}
          onChange={(page) => update({ page: String(page) })}
        />
      )}
    </>
  );
}

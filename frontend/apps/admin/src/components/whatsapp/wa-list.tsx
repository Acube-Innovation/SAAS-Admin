"use client";

import { Alert, Button, type Column, DataTable, SelectField, StatusBadge, TextField } from "@saas/ui";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { errorMessage, t } from "@/i18n";
import { useWhatsAppTemplates } from "@/lib/queries";
import type { WhatsAppTemplate } from "@/lib/types";

const STATUSES = ["draft", "sent_to_client", "signed_off", "sent_back", "submitted", "approved", "rejected", "paused", "disabled"];

export function WhatsAppList() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = { search: params.get("search") ?? "", status: params.get("status") ?? "", page: params.get("page") ?? "1" };
  const [search, setSearch] = useState(filters.search);
  const templates = useWhatsAppTemplates(filters);

  function update(changes: Partial<typeof filters>) {
    const next = new URLSearchParams({ ...filters, page: "1", ...changes });
    for (const [key, value] of [...next.entries()]) if (!value || (key === "page" && value === "1")) next.delete(key);
    router.replace(next.size ? `${pathname}?${next}` : pathname);
  }

  const columns: Column<WhatsAppTemplate>[] = [
    {
      key: "name",
      header: t("wa.col.name"),
      render: (row) => (
        <Link href={`/whatsapp-templates/${row.id}`} className="font-mono text-xs font-medium text-ink hover:text-brand-700">
          {row.name}
        </Link>
      ),
    },
    { key: "client", header: t("wa.col.client"), render: (row) => row.tenant_name },
    { key: "language", header: t("wa.col.language"), render: (row) => row.language.toUpperCase() },
    {
      key: "latest",
      header: t("wa.col.latest"),
      render: (row) => {
        const latest = row.latest_version as { version: number; status: string };
        return (
          <span className="flex items-center gap-2">
            v{latest.version} <StatusBadge status={latest.status} label={t(`status.${latest.status}`)} />
          </span>
        );
      },
    },
    { key: "live", header: t("wa.col.live"), render: (row) => (row.live_version ? `v${row.live_version}` : t("common.none")) },
  ];

  return (
    <>
      <PageHeader
        title={t("page.whatsapp_templates.title")}
        description={t("page.whatsapp_templates.description")}
        actions={
          <Link href="/whatsapp-templates/new">
            <Button>{t("wa.new")}</Button>
          </Link>
        }
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_14rem]">
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
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onBlur={() => search.trim() !== filters.search && update({ search: search.trim() })}
          />
        </form>
        <SelectField
          label={t("wa.filter_status")}
          value={filters.status}
          placeholder={t("clients.all")}
          options={STATUSES.map((s) => ({ value: s, label: t(`status.${s}`) }))}
          onChange={(event) => update({ status: event.target.value })}
        />
      </div>
      {templates.isError && <Alert>{errorMessage(templates.error)}</Alert>}
      <DataTable
        caption={t("page.whatsapp_templates.title")}
        columns={columns}
        rows={templates.data?.results}
        rowKey={(row) => row.id}
        loading={templates.isPending}
        loadingLabel={t("app.loading")}
        empty={t("wa.empty")}
        onRowClick={(row) => router.push(`/whatsapp-templates/${row.id}`)}
      />
      {templates.data && templates.data.count > 0 && (
        <Pagination page={Number(filters.page)} count={templates.data.count} onChange={(page) => update({ page: String(page) })} />
      )}
    </>
  );
}

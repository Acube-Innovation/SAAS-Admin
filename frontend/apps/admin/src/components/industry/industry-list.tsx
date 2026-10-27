"use client";

import { Alert, Button, type Column, DataTable, StatusPill } from "@saas/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/page-header";
import { errorMessage, t } from "@/i18n";
import { useMe } from "@/lib/api";
import { useIndustryTemplates } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";
import type { IndustryTemplate } from "@/lib/types";

export function IndustryList() {
  const router = useRouter();
  const me = useMe();
  const templates = useIndustryTemplates();
  const columns: Column<IndustryTemplate>[] = [
    {
      key: "name",
      header: t("industry.col.name"),
      render: (row) => (
        <Link href={`/industry-templates/${row.id}`} className="font-medium text-ink hover:text-brand-700">
          {row.name}
        </Link>
      ),
    },
    { key: "key", header: t("industry.col.key"), render: (row) => <span className="font-mono text-xs">{row.key}</span> },
    { key: "version", header: t("industry.col.version"), render: (row) => `v${row.current_version}` },
    { key: "clients", header: t("industry.col.clients"), render: (row) => row.clients_using },
    {
      key: "active",
      header: t("industry.col.active"),
      render: (row) => (
        <StatusPill tone={row.is_active ? "ok" : "neutral"}>{row.is_active ? t("common.yes") : t("common.no")}</StatusPill>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title={t("page.industry_templates.title")}
        description={t("page.industry_templates.description")}
        actions={
          isSuperAdmin(me.data) && (
            <Link href="/industry-templates/new">
              <Button>{t("industry.new")}</Button>
            </Link>
          )
        }
      />
      {templates.isError && <Alert>{errorMessage(templates.error)}</Alert>}
      <DataTable
        caption={t("page.industry_templates.title")}
        columns={columns}
        rows={templates.data}
        rowKey={(row) => row.id}
        loading={templates.isPending}
        loadingLabel={t("app.loading")}
        empty={t("industry.empty")}
        onRowClick={(row) => router.push(`/industry-templates/${row.id}`)}
      />
    </>
  );
}

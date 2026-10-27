"use client";

import { ApiError, unwrap } from "@saas/api-client";
import { Alert, Button, EmptyState, ReasonDialog, Spinner, StatusBadge } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Tabs } from "@/components/tabs";
import { errorMessage, t } from "@/i18n";
import { api, useMe } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { keys, useTenant } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";

import { ActivityTab } from "./tab-activity";
import { DetailsTab } from "./tab-details";
import { SettingsTab } from "./tab-settings";
import { TemplatesTab } from "./tab-templates";
import { UsersTab } from "./tab-users";

const TABS = ["details", "users", "settings", "templates", "activity"] as const;
type Tab = (typeof TABS)[number];
type Dialog = "suspend" | "activate" | "support" | null;

/** Client detail (spec 2.5) with suspend / activate (3.2) and support access (3.3). */
export function ClientDetail({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const me = useMe();
  const tenant = useTenant(id);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.includes(requested) ? requested : "details";
  const superAdmin = isSuperAdmin(me.data);

  if (tenant.isPending) return <Spinner label={t("app.loading")} />;
  if (tenant.isError) {
    const notFound = tenant.error instanceof ApiError && tenant.error.status === 404;
    return notFound ? (
      <EmptyState title={t("client.not_found")} action={<Link href="/clients">{t("common.back")}</Link>} />
    ) : (
      <Alert>{errorMessage(tenant.error)}</Alert>
    );
  }
  const client = tenant.data;
  const suspended = client.status === "suspended";

  async function act(kind: Exclude<Dialog, null>, reason: string) {
    setBusy(true);
    setDialogError(undefined);
    try {
      if (kind === "support") {
        const session = await unwrap(
          api.POST("/api/admin/tenants/{id}/support-sessions", { params: { path: { id } }, body: { reason } }),
        );
        queryClient.setQueryData(keys.supportSession, session);
        setNotice(t("support.started", { tenant: client.name }));
      } else {
        const path = kind === "suspend" ? "/api/admin/tenants/{id}/suspend" : "/api/admin/tenants/{id}/activate";
        const updated = await unwrap(api.POST(path, { params: { path: { id } }, body: { reason } }));
        queryClient.setQueryData(keys.tenant(id), updated);
        await queryClient.invalidateQueries({ queryKey: ["tenants"] });
        await queryClient.invalidateQueries({ queryKey: ["tenant", id, "activity"] });
        setNotice(t(kind === "suspend" ? "client.suspended_done" : "client.activated_done"));
      }
      setDialog(null);
    } catch (caught) {
      setDialogError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const dialogText = {
    suspend: { title: t("client.suspend_title", { name: client.name }), body: t("client.suspend_body"), confirm: t("client.suspend") },
    activate: { title: t("client.activate_title", { name: client.name }), body: t("client.activate_body"), confirm: t("client.activate") },
    support: { title: t("client.support_title", { name: client.name }), body: t("client.support_body"), confirm: t("client.open_support") },
  };

  return (
    <>
      <Link href="/clients" className="text-sm font-medium text-brand-600 hover:text-brand-700">
        ← {t("page.clients.title")}
      </Link>
      <div className="mt-3 mb-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight text-ink">{client.name}</h1>
            <StatusBadge status={client.status} label={t(`status.${client.status}`)} />
          </div>
          <p className="mt-1 text-sm text-muted">
            {client.legal_name} · {client.industry_template_name ?? t("common.none")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setDialog("support")}>
            {t("client.open_support")}
          </Button>
          {superAdmin &&
            (suspended ? (
              <Button onClick={() => setDialog("activate")}>{t("client.activate")}</Button>
            ) : (
              <Button variant="danger" onClick={() => setDialog("suspend")}>
                {t("client.suspend")}
              </Button>
            ))}
        </div>
      </div>

      <div className="space-y-3">
        {notice && <Alert tone="success">{notice}</Alert>}
        {params.get("created") && !notice && (
          <Alert tone="success">{t("client.created", { email: client.owner_email ?? "" })}</Alert>
        )}
        {suspended && (
          <Alert tone="warn" title={t("client.suspended_banner_title", { date: formatDateTime(client.suspended_at) })}>
            {t("client.suspended_banner_body", { reason: client.suspension_reason })}
          </Alert>
        )}
      </div>

      <div className="mt-5">
        <Tabs
          label={t("client.tabs")}
          active={tab}
          tabs={TABS.map((id) => ({ id, label: t(`client.tab.${id}`) }))}
          onChange={(next) => router.replace(`${pathname}?tab=${next}`)}
        />
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="mt-5">
          {tab === "details" && <DetailsTab client={client} editable={superAdmin} />}
          {tab === "users" && <UsersTab tenantId={id} />}
          {tab === "settings" && <SettingsTab client={client} editable={superAdmin} />}
          {tab === "templates" && <TemplatesTab tenantId={id} />}
          {tab === "activity" && <ActivityTab tenantId={id} />}
        </div>
      </div>

      {dialog && (
        <ReasonDialog
          open
          title={dialogText[dialog].title}
          description={dialogText[dialog].body}
          label={t("client.reason")}
          confirmLabel={dialogText[dialog].confirm}
          cancelLabel={t("common.cancel")}
          messages={{ tooShort: t("validation.reason_min"), tooLong: t("validation.reason_max") }}
          danger={dialog === "suspend"}
          busy={busy}
          error={dialogError}
          onSubmit={(reason) => act(dialog, reason)}
          onClose={() => {
            setDialog(null);
            setDialogError(undefined);
          }}
        />
      )}
    </>
  );
}

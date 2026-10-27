"use client";

import { ApiError, unwrap } from "@saas/api-client";
import { Alert, Button, ReasonDialog, Spinner, StatusBadge, cn } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { errorMessage, t } from "@/i18n";
import { api, useMe } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useReferenceData, useTemplateVariables, useWhatsAppTemplate, useWhatsAppTemplates } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";
import type { TemplateDetail, TemplateVersion } from "@/lib/types";

import { type Content, withKnownSamples } from "./content";
import { ContentEditor, contentErrors } from "./content-editor";
import { DevPanel } from "./dev-panel";
import { WhatsAppPreview } from "./preview";
import { SignoffDialog } from "./signoff-dialog";
import { StatusSteps } from "./status-steps";

const EDITABLE = new Set(["draft", "sent_back"]);
const SUPER_ADMIN_ACTIONS = new Set(["manual_signoff", "submit", "make_live", "disable"]);

function contentOf(version: TemplateVersion): Content {
  return {
    header_text: version.header_text,
    body: version.body,
    footer: version.footer,
    buttons: version.buttons as Content["buttons"],
    sample_values: version.sample_values as Record<string, string>,
  };
}

/** WhatsApp template editor (spec 2.7) with the Workflow 3.4 actions. */
export function WhatsAppEditor({ id }: { id: string }) {
  const template = useWhatsAppTemplate(id);
  if (template.isPending) return <Spinner label={t("app.loading")} />;
  if (template.isError) return <Alert>{errorMessage(template.error)}</Alert>;
  return <Loaded template={template.data} />;
}

function Loaded({ template }: { template: TemplateDetail }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const me = useMe();
  const reference = useReferenceData();
  const variables = useTemplateVariables(template.schedule_type);
  const siblings = useWhatsAppTemplates({ tenant: template.tenant_id, search: template.name });
  const superAdmin = isSuperAdmin(me.data);
  const versions = template.versions;
  const selected = versions.find((v) => v.id === params.get("v")) ?? versions[0]!;
  const [content, setContent] = useState(() => contentOf(selected));
  const [errors, setErrors] = useState<ReturnType<typeof contentErrors>>({});
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string }>();
  const [busy, setBusy] = useState<string>();
  const [dialog, setDialog] = useState<"signoff" | "disable" | null>(null);

  // Start again from the server's content when another version is selected or this one changes on
  // the server (a new event). Refetches with no change keep unsaved edits.
  const versionKey = `${selected.id}:${selected.events.length}`;
  const [loadedKey, setLoadedKey] = useState(versionKey);
  if (loadedKey !== versionKey) {
    setLoadedKey(versionKey);
    setContent(contentOf(selected));
    setErrors({});
  }

  const editable = EDITABLE.has(selected.status);
  const dirty = JSON.stringify(content) !== JSON.stringify(contentOf(selected));

  function refresh(version?: TemplateVersion) {
    void queryClient.invalidateQueries({ queryKey: ["whatsapp-template", template.id] });
    void queryClient.invalidateQueries({ queryKey: ["whatsapp-templates"] });
    if (version && version.id !== selected.id) router.replace(`${pathname}?v=${version.id}`);
  }

  async function run(name: string, call: () => Promise<TemplateVersion>, success?: string) {
    setBusy(name);
    setNotice(undefined);
    try {
      const version = await call();
      refresh(version);
      if (success) setNotice({ tone: "success", text: success });
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "validation_error") setErrors(contentErrors(caught.fields));
      setNotice({ tone: "error", text: errorMessage(caught) });
    } finally {
      setBusy(undefined);
    }
  }

  const path = { params: { path: { id: template.id, vid: selected.id } } };
  const actions: { name: string; label: string; variant?: "primary" | "secondary" | "danger"; onClick: () => void }[] = [];
  const allowed = (action: string) => selected.available_actions.includes(action) && (superAdmin || !SUPER_ADMIN_ACTIONS.has(action));
  if (allowed("send_to_client"))
    actions.push({
      name: "send",
      label: t("wa.action.send_to_client"),
      onClick: () => run("send", () => unwrap(api.POST("/api/admin/whatsapp-templates/{id}/versions/{vid}/send-to-client", path))),
    });
  if (allowed("manual_signoff")) actions.push({ name: "signoff", label: t("wa.action.manual_signoff"), onClick: () => setDialog("signoff") });
  if (allowed("submit"))
    actions.push({
      name: "submit",
      label: t("wa.action.submit"),
      onClick: () => run("submit", () => unwrap(api.POST("/api/admin/whatsapp-templates/{id}/versions/{vid}/submit", path))),
    });
  if (allowed("make_live"))
    actions.push({
      name: "live",
      label: t("wa.action.make_live"),
      onClick: () => run("live", () => unwrap(api.POST("/api/admin/whatsapp-templates/{id}/versions/{vid}/make-live", path))),
    });
  if (allowed("new_version"))
    actions.push({
      name: "new",
      label: t("wa.action.new_version"),
      variant: "secondary",
      onClick: () => run("new", () => unwrap(api.POST("/api/admin/whatsapp-templates/{id}/versions/{vid}/new-version", path))),
    });
  if (["submitted", "approved", "paused"].includes(selected.status) && selected.provider_template_id)
    actions.push({
      name: "sync",
      label: t("wa.action.sync"),
      variant: "secondary",
      onClick: () => run("sync", () => unwrap(api.POST("/api/admin/whatsapp-templates/{id}/versions/{vid}/sync-status", path))),
    });
  if (allowed("disable")) actions.push({ name: "disable", label: t("wa.action.disable"), variant: "danger", onClick: () => setDialog("disable") });

  const languages = (siblings.data?.results ?? []).filter((s) => s.name === template.name);

  return (
    <>
      <Link href="/whatsapp-templates" className="text-sm font-medium text-brand-600 hover:text-brand-700">
        ← {t("page.whatsapp_templates.title")}
      </Link>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-mono text-xl font-semibold text-ink">{template.name}</h1>
          <p className="mt-1 text-sm text-muted">
            <Link href={`/clients/${template.tenant_id}?tab=templates`} className="hover:text-brand-700">
              {template.tenant_name}
            </Link>{" "}
            · {template.language.toUpperCase()} · {template.schedule_type_name}
          </p>
        </div>
        {languages.length > 1 && (
          <div className="flex items-center gap-2 text-sm" aria-label={t("wa.preview_languages")}>
            {languages.map((sibling) => (
              <Link
                key={sibling.id}
                href={`/whatsapp-templates/${sibling.id}`}
                aria-current={sibling.id === template.id ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1 font-medium",
                  sibling.id === template.id ? "bg-brand-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-300",
                )}
              >
                {sibling.language.toUpperCase()}
              </Link>
            ))}
          </div>
        )}
      </div>

      <div className="mt-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <StatusSteps status={selected.status} isLive={selected.is_live} />
          <span className="text-sm text-muted">v{selected.version}</span>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
        {selected.status === "rejected" && (
          <Alert title={t("wa.rejection")}>{selected.rejection_reason}</Alert>
        )}
        {selected.status === "sent_back" && (
          <Alert tone="warn" title={t("wa.sent_back")}>
            {selected.events.filter((e) => e.action === "client_send_back").at(-1)?.note}
          </Alert>
        )}
        {selected.status === "paused" && <Alert tone="warn">{t("wa.paused")}</Alert>}
      </div>

      <div className="mt-5 grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-5">
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            {!editable && <p className="mb-4 text-sm text-muted">{t("wa.read_only")}</p>}
            <ContentEditor
              content={content}
              onChange={setContent}
              variables={variables.data?.variables ?? []}
              errors={errors}
              disabled={!editable}
              knownSamples={{ agency_name: template.tenant_name }}
            />
            {editable && (
              <div className="mt-5 flex justify-end">
                <Button
                  loading={busy === "save"}
                  disabled={!dirty}
                  onClick={() =>
                    run(
                      "save",
                      async () => {
                        setErrors({});
                        return unwrap(
                          api.PATCH("/api/admin/whatsapp-templates/{id}/versions/{vid}", {
                            ...path,
                            body: withKnownSamples(content, template.tenant_name),
                          }),
                        );
                      },
                      t("wa.saved"),
                    )
                  }
                >
                  {t("wa.save")}
                </Button>
              </div>
            )}
          </section>
          {actions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {actions.map((action) => (
                <Button
                  key={action.name}
                  variant={action.variant ?? "primary"}
                  loading={busy === action.name}
                  disabled={dirty && action.name !== "disable"}
                  onClick={action.onClick}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          )}
          {superAdmin && reference.data?.whatsapp_dev_controls && <DevPanel version={selected} onChange={(v) => refresh(v)} />}
        </div>

        <aside className="space-y-5">
          <WhatsAppPreview content={content} agencyName={template.tenant_name} />
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-ink">{t("wa.versions")}</h2>
            <ul className="mt-2 space-y-1">
              {versions.map((version) => (
                <li key={version.id}>
                  <Link
                    href={`${pathname}?v=${version.id}`}
                    aria-current={version.id === selected.id ? "true" : undefined}
                    className={cn(
                      "flex items-center justify-between rounded-lg px-2 py-1.5 text-sm",
                      version.id === selected.id ? "bg-slate-100 font-medium" : "hover:bg-slate-50",
                    )}
                  >
                    <span>v{version.version}</span>
                    <span className="flex items-center gap-1.5">
                      {version.is_live && <StatusBadge status="live" label={t("status.live")} />}
                      <StatusBadge status={version.status} label={t(`status.${version.status}`)} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-ink">{t("wa.history")}</h2>
            <ol className="mt-3 space-y-3 border-l border-slate-200 pl-4">
              {[...selected.events].reverse().map((event, index) => (
                <li key={index} className="text-sm">
                  <p className="font-medium text-ink">{t(`wa.event.${event.action}`)}</p>
                  <p className="text-xs text-muted">
                    {formatDateTime(event.created_at)} · {event.actor_email ?? t("activity.system")}
                  </p>
                  {event.note && <p className="mt-0.5 text-xs text-slate-600">{event.note}</p>}
                </li>
              ))}
            </ol>
            {selected.has_evidence && (
              <Button
                variant="secondary"
                className="mt-4"
                onClick={async () => {
                  const { url } = await unwrap(
                    api.GET("/api/admin/whatsapp-templates/{id}/versions/{vid}/evidence", path),
                  );
                  window.open(url, "_blank", "noopener");
                }}
              >
                {t("wa.evidence")}
              </Button>
            )}
            {selected.provider_template_id && (
              <p className="mt-3 text-xs text-muted">
                {t("wa.provider_id")}: <span className="font-mono">{selected.provider_template_id}</span>
              </p>
            )}
          </section>
        </aside>
      </div>

      <SignoffDialog
        open={dialog === "signoff"}
        templateId={template.id}
        versionId={selected.id}
        onClose={() => setDialog(null)}
        onDone={(version) => {
          setDialog(null);
          refresh(version);
        }}
      />
      {dialog === "disable" && (
        <ReasonDialog
          open
          danger
          title={t("wa.disable_title")}
          description={t("wa.disable_body")}
          label={t("client.reason")}
          confirmLabel={t("wa.action.disable")}
          cancelLabel={t("common.cancel")}
          messages={{ tooShort: t("validation.reason_min"), tooLong: t("validation.reason_max") }}
          busy={busy === "disable"}
          onSubmit={(reason) => {
            setDialog(null);
            void run("disable", () =>
              unwrap(api.POST("/api/admin/whatsapp-templates/{id}/versions/{vid}/disable", { ...path, body: { reason } })),
            );
          }}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}

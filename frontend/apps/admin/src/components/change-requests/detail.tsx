"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, ReasonDialog, SelectField, Spinner, StatusPill, TextAreaField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { errorMessage, t } from "@/i18n";
import { api, ensureCsrf } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { keys, useAssignees, useCandidateVersions, useChangeRequest } from "@/lib/queries";

import { REQUEST_TONES } from "./labels";

/** Change request detail (spec 2.9, Workflow 3.6): assign, start work, reply, link a draft, decline. */
export function ChangeRequestDetail({ id }: { id: string }) {
  const request = useChangeRequest(id);
  const assignees = useAssignees();
  const closed = request.data?.status === "done" || request.data?.status === "declined";
  const candidates = useCandidateVersions(id, Boolean(request.data) && !closed);
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [reply, setReply] = useState("");
  const [replyError, setReplyError] = useState<string>();
  const [version, setVersion] = useState("");
  const [declining, setDeclining] = useState(false);

  async function run(kind: string, call: () => Promise<unknown>, message?: string) {
    setBusy(kind);
    setError(undefined);
    setNotice(undefined);
    try {
      await ensureCsrf();
      await call();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.changeRequests }),
        queryClient.invalidateQueries({ queryKey: keys.dashboard }),
      ]);
      if (message) setNotice(message);
      return true;
    } catch (caught) {
      setError(errorMessage(caught));
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (request.isPending) return <Spinner label={t("app.loading")} />;
  if (request.isError) return <Alert>{errorMessage(request.error)}</Alert>;
  const data = request.data;
  const path = { params: { path: { id } } };

  async function sendReply() {
    if (!reply.trim()) {
      setReplyError(t("cr.reply_required"));
      return;
    }
    if (reply.length > 2000) {
      setReplyError(t("cr.reply_too_long"));
      return;
    }
    setReplyError(undefined);
    const ok = await run("reply", () => unwrap(api.POST("/api/admin/change-requests/{id}/comments", { ...path, body: { body: reply.trim() } })));
    if (ok) setReply("");
  }

  return (
    <>
      <Link href="/change-requests" className="mb-4 inline-block text-sm text-brand-700 hover:underline">← {t("page.change_requests.title")}</Link>
      <PageHeader
        title={data.subject}
        description={t("cr.meta", {
          type: t(`cr.type.${data.request_type}`),
          client: data.tenant_name,
          email: data.requested_by_email,
          date: formatDateTime(data.created_at),
        })}
        actions={<StatusPill tone={REQUEST_TONES[data.status] ?? "neutral"}>{t(`cr.status.${data.status}`)}</StatusPill>}
      />
      {notice && <div className="mb-4"><Alert tone="success">{notice}</Alert></div>}
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <p className="whitespace-pre-line rounded-xl border border-slate-200 bg-white p-4 text-sm text-ink">
            {data.description || t("cr.no_description")}
          </p>
          <section aria-label={t("cr.thread")} className="space-y-3">
            <h2 className="text-sm font-semibold text-ink">{t("cr.thread")}</h2>
            {data.comments.length === 0 && <p className="text-sm text-muted">{t("cr.no_comments")}</p>}
            <ol className="space-y-3">
              {data.comments.map((comment) => (
                <li
                  key={comment.id}
                  data-from={comment.from_staff ? "staff" : "client"}
                  className={comment.from_staff ? "ml-8 rounded-xl bg-brand-50 p-3" : "mr-8 rounded-xl border border-slate-200 bg-white p-3"}
                >
                  <p className="text-xs text-muted">
                    {comment.from_staff ? t("cr.from_staff", { email: comment.author_email }) : t("cr.from_client", { email: comment.author_email })} ·{" "}
                    {formatDateTime(comment.created_at)}
                  </p>
                  <p className="mt-1 whitespace-pre-line text-sm text-ink">{comment.body}</p>
                </li>
              ))}
            </ol>
            {!closed && (
              <form
                aria-label={t("cr.reply")}
                className="space-y-2"
                onSubmit={(e) => { e.preventDefault(); void sendReply(); }}
              >
                <TextAreaField label={t("cr.reply")} hint={t("cr.reply_hint")} value={reply} error={replyError} onChange={(e) => setReply(e.target.value)} />
                <Button type="submit" loading={busy === "reply"}>{t("cr.send_reply")}</Button>
              </form>
            )}
          </section>
        </div>

        <aside className="space-y-4">
          <section aria-label={t("cr.work")} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
            <SelectField
              label={t("cr.col.assignee")}
              value={data.assignee?.id ?? ""}
              placeholder={t("cr.unassigned")}
              disabled={closed || busy !== null}
              options={(assignees.data ?? []).map((u) => ({ value: u.id, label: u.full_name || u.email }))}
              onChange={(e) => {
                const assignee = e.target.value || null; // read now: the controlled select resets on re-render
                void run("assign", () => unwrap(api.PATCH("/api/admin/change-requests/{id}", { ...path, body: { assignee } })), t("cr.assigned_done"));
              }}
            />
            {data.status === "open" && (
              <Button
                className="w-full"
                loading={busy === "start"}
                onClick={() => run("start", () => unwrap(api.PATCH("/api/admin/change-requests/{id}", { ...path, body: { status: "in_progress" } })), t("cr.started_done"))}
              >
                {t("cr.start_work")}
              </Button>
            )}
            {data.started_at && <p className="text-xs text-muted">{t("cr.started_on", { date: formatDateTime(data.started_at) })}</p>}
            {!closed && (
              <Button variant="secondary" className="w-full" onClick={() => setDeclining(true)}>{t("cr.decline")}</Button>
            )}
            {data.status === "declined" && (
              <p className="text-sm text-ink">
                <span className="text-muted">{t("cr.decline_reason")}: </span>
                {data.decline_reason}
              </p>
            )}
            {data.closed_at && <p className="text-xs text-muted">{t("cr.closed_on", { date: formatDateTime(data.closed_at) })}</p>}
          </section>

          <section aria-label={t("cr.linked_version")} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-ink">{t("cr.linked_version")}</h2>
            {data.linked_version ? (
              <div className="text-sm" data-linked-version={data.linked_version.version}>
                <p className="font-mono text-ink">{data.linked_version.template_name}</p>
                <p className="text-muted">
                  {data.linked_version.language.toUpperCase()} · v{data.linked_version.version} ·{" "}
                  {data.linked_version.is_live ? t("status.live") : t(`status.${data.linked_version.status}`)}
                </p>
                <Link
                  href={`/whatsapp-templates/${data.linked_version.template_id}?v=${data.linked_version.id}`}
                  className="mt-1 inline-block font-medium text-brand-700 hover:underline"
                >
                  {t("cr.open_version")}
                </Link>
              </div>
            ) : (
              <p className="text-sm text-muted">{t("cr.no_linked_version")}</p>
            )}
            {!closed && (
              <>
                <SelectField
                  label={data.linked_version ? t("cr.change_version") : t("cr.pick_version")}
                  value={version}
                  placeholder={t("cr.choose")}
                  options={(candidates.data ?? []).map((v) => ({
                    value: v.id,
                    label: `${v.template_name} · ${v.language.toUpperCase()} · v${v.version} (${t(`status.${v.status}`)})`,
                  }))}
                  onChange={(e) => setVersion(e.target.value)}
                />
                {candidates.data?.length === 0 && <p className="text-xs text-muted">{t("cr.no_candidates")}</p>}
                <Button
                  variant="secondary"
                  className="w-full"
                  disabled={!version}
                  loading={busy === "link"}
                  onClick={async () => {
                    const ok = await run("link", () => unwrap(api.POST("/api/admin/change-requests/{id}/link-version", { ...path, body: { version } })), t("cr.linked_done"));
                    if (ok) setVersion("");
                  }}
                >
                  {t("cr.link")}
                </Button>
                <p className="text-xs text-muted">{t("cr.link_hint")}</p>
              </>
            )}
          </section>
        </aside>
      </div>

      <ReasonDialog
        open={declining}
        title={t("cr.decline_title")}
        description={t("cr.decline_hint")}
        label={t("cr.decline_reason")}
        confirmLabel={t("cr.decline")}
        cancelLabel={t("common.cancel")}
        messages={{ tooShort: t("validation.reason_min"), tooLong: t("validation.reason_max") }}
        danger
        busy={busy === "decline"}
        onSubmit={async (reason) => {
          const ok = await run("decline", () => unwrap(api.POST("/api/admin/change-requests/{id}/decline", { ...path, body: { reason } })), t("cr.declined_done"));
          if (ok) setDeclining(false);
        }}
        onClose={() => setDeclining(false)}
      />
    </>
  );
}

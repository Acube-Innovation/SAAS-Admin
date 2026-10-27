"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, TextField } from "@saas/ui";
import { useState } from "react";

import { errorMessage, t } from "@/i18n";
import { api } from "@/lib/api";
import type { TemplateVersion } from "@/lib/types";

type Action = "client_approve" | "client_send_back" | "provider_approve" | "provider_reject" | "provider_pause";

const FOR_STATUS: Record<string, Action[]> = {
  sent_to_client: ["client_approve", "client_send_back"],
  submitted: ["provider_approve", "provider_reject"],
  approved: ["provider_pause"],
  paused: ["provider_approve"],
};

/**
 * Development only (stub provider + DEBUG, checked by the API too): play the client and Meta to
 * walk a template through every Workflow 3.4 path before the portal and Meta are connected.
 */
export function DevPanel({ version, onChange }: { version: TemplateVersion; onChange: (v: TemplateVersion) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<Action>();
  const [error, setError] = useState<string>();
  const actions = FOR_STATUS[version.status] ?? [];
  if (actions.length === 0) return null;

  async function run(action: Action) {
    setBusy(action);
    setError(undefined);
    try {
      const updated = await unwrap(
        api.POST("/api/admin/dev/whatsapp-versions/{vid}/simulate", {
          params: { path: { vid: version.id } },
          body: { action, reason },
        }),
      );
      setReason("");
      onChange(updated);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <section aria-label={t("dev.title")} className="rounded-xl border-2 border-dashed border-violet-300 bg-violet-50/60 p-4">
      <h3 className="text-sm font-semibold text-violet-900">{t("dev.title")}</h3>
      <p className="mt-1 text-xs text-violet-800">{t("dev.body")}</p>
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      {(actions.includes("client_send_back") || actions.includes("provider_reject")) && (
        <div className="mt-3">
          <TextField label={t("dev.reason")} value={reason} onChange={(event) => setReason(event.target.value)} />
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {actions.map((action) => (
          <Button key={action} variant="secondary" loading={busy === action} onClick={() => run(action)}>
            {t(`dev.${action}`)}
          </Button>
        ))}
      </div>
    </section>
  );
}

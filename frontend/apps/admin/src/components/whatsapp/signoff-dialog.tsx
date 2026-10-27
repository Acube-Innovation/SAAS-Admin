"use client";

import { ApiError, readCookie } from "@saas/api-client";
import { Button, Dialog, FileUpload, TextAreaField } from "@saas/ui";
import { useState } from "react";

import { errorMessage, t } from "@/i18n";

import type { TemplateVersion } from "@/lib/types";

export const EVIDENCE_TYPES = [".pdf", ".png", ".jpg", ".jpeg"];
export const EVIDENCE_MAX = 5 * 1024 * 1024;

/** Manual sign-off with evidence (super admin only, Workflow 3.4): multipart upload. */
export function SignoffDialog({
  open,
  templateId,
  versionId,
  onDone,
  onClose,
}: {
  open: boolean;
  templateId: string;
  versionId: string;
  onDone: (version: TemplateVersion) => void;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!file) return setError(t("wa.evidence_required"));
    setBusy(true);
    setError(undefined);
    const form = new FormData();
    form.append("evidence", file);
    form.append("note", note);
    try {
      const response = await fetch(`/api/admin/whatsapp-templates/${templateId}/versions/${versionId}/manual-signoff`, {
        method: "POST",
        body: form,
        credentials: "include",
        headers: { "X-CSRFToken": readCookie("csrftoken") ?? "" },
      });
      const body = await response.json();
      if (!response.ok) {
        const fieldMessage = Array.isArray(body?.fields?.evidence) ? body.fields.evidence.join(" ") : undefined;
        throw new ApiError(response.status, fieldMessage ? { ...body, code: "evidence", detail: fieldMessage } : body);
      }
      onDone(body);
    } catch (caught) {
      setError(caught instanceof ApiError && caught.code === "evidence" ? caught.message : errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      title={t("wa.signoff_title")}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} loading={busy}>
            {t("wa.signoff_submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p>{t("wa.signoff_body")}</p>
        <FileUpload
          label={t("wa.signoff_evidence")}
          accept={EVIDENCE_TYPES}
          maxBytes={EVIDENCE_MAX}
          messages={{
            wrongType: t("wa.evidence_wrong_type"),
            tooLarge: t("wa.evidence_too_large"),
            choose: t("wa.choose_file"),
            replace: t("wa.replace_file"),
          }}
          error={error}
          onChange={setFile}
        />
        <TextAreaField label={t("wa.signoff_note")} value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} />
      </div>
    </Dialog>
  );
}

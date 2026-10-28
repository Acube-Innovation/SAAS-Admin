"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId, useRef } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "./button";
import { TextAreaField } from "./fields";

type DialogProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** "lg" for wide content such as tables. */
  size?: "md" | "lg";
};

/** Accessible modal: focus moves inside, Escape and the backdrop close it. */
export function Dialog({ open, title, onClose, children, footer, size = "md" }: DialogProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  // Callers often pass a new onClose each render; keep the latest without re-running the focus
  // effect (which would pull focus back to the first field while the user types).
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const focusable = panel.current?.querySelector<HTMLElement>(
      "textarea, input, select, button:not([data-dialog-close])",
    );
    focusable?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div aria-hidden className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`relative max-h-[90vh] w-full overflow-y-auto rounded-xl bg-white p-6 shadow-xl ${size === "lg" ? "max-w-3xl" : "max-w-md"}`}
      >
        <h2 id={titleId} className="text-base font-semibold text-ink">
          {title}
        </h2>
        <div className="mt-3 text-sm text-slate-600">{children}</div>
        {footer && <div className="mt-6 flex justify-end gap-3">{footer}</div>}
      </div>
    </div>
  );
}

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  danger,
  busy,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {message}
    </Dialog>
  );
}

type ReasonDialogProps = {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  label: string;
  confirmLabel: string;
  cancelLabel: string;
  /** Translated messages for the 5-500 character rule (Workflows 3.2 and 3.3). */
  messages: { tooShort: string; tooLong: string };
  danger?: boolean;
  busy?: boolean;
  error?: string;
  onSubmit: (reason: string) => void;
  onClose: () => void;
};

/** Asks for a reason of 5-500 characters before suspend, activate or support access. */
export function ReasonDialog({
  open,
  title,
  description,
  label,
  confirmLabel,
  cancelLabel,
  messages,
  danger,
  busy,
  error,
  onSubmit,
  onClose,
}: ReasonDialogProps) {
  const schema = z.object({
    reason: z.string().trim().min(5, messages.tooShort).max(500, messages.tooLong),
  });
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { reason: "" },
  });
  const formId = useId();

  useEffect(() => {
    if (!open) form.reset({ reason: "" });
  }, [open, form]);

  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button type="submit" form={formId} variant={danger ? "danger" : "primary"} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="space-y-4"
        onSubmit={form.handleSubmit(({ reason }) => onSubmit(reason))}
      >
        {description && <div>{description}</div>}
        <TextAreaField
          label={label}
          required
          error={form.formState.errors.reason?.message ?? error}
          {...form.register("reason")}
        />
      </form>
    </Dialog>
  );
}

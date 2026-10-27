"use client";

import { useId, useState } from "react";

import { cn } from "./cn";

type Props = {
  label: string;
  hint?: string;
  /** Allowed extensions, lower case with the dot, e.g. [".pdf", ".png", ".jpg"]. */
  accept: string[];
  maxBytes: number;
  messages: { wrongType: string; tooLarge: string; choose: string; replace: string };
  error?: string;
  onChange: (file: File | null) => void;
};

/** File picker that checks type and size before anything is uploaded (e.g. sign-off evidence). */
export function FileUpload({ label, hint, accept, maxBytes, messages, error, onChange }: Props) {
  const id = useId();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | undefined>();

  function handle(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    if (!chosen) return;
    const extension = chosen.name.slice(chosen.name.lastIndexOf(".")).toLowerCase();
    let issue: string | undefined;
    if (!accept.includes(extension)) issue = messages.wrongType;
    else if (chosen.size > maxBytes) issue = messages.tooLarge;
    setProblem(issue);
    setFile(issue ? null : chosen);
    onChange(issue ? null : chosen);
    if (issue) event.target.value = "";
  }

  const shown = problem ?? error;
  return (
    <div className="space-y-1.5">
      <span className="block text-sm font-medium text-ink">{label}</span>
      <label
        htmlFor={id}
        className={cn(
          "flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-dashed px-4 py-3 text-sm",
          shown ? "border-red-400 bg-red-50/40" : "border-slate-300 bg-white hover:bg-slate-50",
        )}
      >
        <span className="truncate text-ink">{file ? file.name : messages.choose}</span>
        <span className="shrink-0 font-semibold text-brand-600">
          {file ? messages.replace : messages.choose}
        </span>
        <input
          id={id}
          type="file"
          accept={accept.join(",")}
          className="sr-only"
          aria-invalid={shown ? true : undefined}
          onChange={handle}
        />
      </label>
      {shown ? (
        <p className="text-sm text-red-600">{shown}</p>
      ) : (
        hint && <p className="text-xs text-muted">{hint}</p>
      )}
    </div>
  );
}

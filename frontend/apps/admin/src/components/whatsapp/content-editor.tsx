"use client";

import { Button, SelectField, cn } from "@saas/ui";
import { useRef } from "react";

import { t } from "@/i18n";

import { type Content, LIMITS, usedVariables } from "./content";

type FieldErrors = Partial<Record<keyof Content, string>>;

function Counter({ count, max }: { count: number; max: number }) {
  return (
    <span className={cn("text-xs tabular-nums", count > max ? "font-semibold text-red-600" : "text-muted")}>
      {t("wa.counter", { count, max })}
    </span>
  );
}

const box =
  "block w-full rounded-lg border-0 px-3 py-2 text-sm shadow-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600 disabled:bg-slate-50 disabled:text-slate-500";

/** Template content with live counters, a variables picker and sample values (spec 2.7). */
export function ContentEditor({
  content,
  onChange,
  variables,
  errors = {},
  disabled = false,
  knownSamples = {},
}: {
  content: Content;
  onChange: (content: Content) => void;
  variables: string[];
  errors?: FieldErrors;
  disabled?: boolean;
  /** Samples filled in automatically when left empty (shown as placeholders). */
  knownSamples?: Record<string, string>;
}) {
  const body = useRef<HTMLTextAreaElement>(null);
  const header = useRef<HTMLInputElement>(null);
  const lastFocused = useRef<"body" | "header_text">("body");
  const used = usedVariables(content);
  const set = (patch: Partial<Content>) => onChange({ ...content, ...patch });

  function insert(variable: string) {
    const field = lastFocused.current;
    const element = field === "body" ? body.current : header.current;
    const value = content[field];
    const token = `{{${variable}}}`;
    const start = element?.selectionStart ?? value.length;
    const end = element?.selectionEnd ?? value.length;
    set({ [field]: value.slice(0, start) + token + value.slice(end) } as Partial<Content>);
    requestAnimationFrame(() => {
      element?.focus();
      element?.setSelectionRange(start + token.length, start + token.length);
    });
  }

  const label = (text: string, field: keyof Content, max?: number, count?: number) => (
    <div className="mb-1.5 flex items-center justify-between">
      <label htmlFor={`wa-${field}`} className="text-sm font-medium text-ink">
        {text}
      </label>
      {max !== undefined && <Counter count={count ?? 0} max={max} />}
    </div>
  );
  const error = (field: keyof Content) =>
    errors[field] ? <p className="mt-1 text-sm text-red-600">{errors[field]}</p> : null;

  return (
    <fieldset disabled={disabled} className="space-y-5">
      <div>
        {label(t("wa.header"), "header_text", LIMITS.header_text, content.header_text.length)}
        <input
          id="wa-header_text"
          ref={header}
          className={box}
          value={content.header_text}
          onFocus={() => (lastFocused.current = "header_text")}
          onChange={(event) => set({ header_text: event.target.value })}
        />
        {error("header_text")}
      </div>

      <div>
        {label(t("wa.body"), "body", LIMITS.body, content.body.length)}
        <textarea
          id="wa-body"
          ref={body}
          rows={6}
          className={box}
          value={content.body}
          onFocus={() => (lastFocused.current = "body")}
          onChange={(event) => set({ body: event.target.value })}
        />
        {error("body")}
        {!disabled && (
          <div className="mt-2">
            <p className="text-xs font-medium text-muted">
              {t("wa.variables")} · {t("wa.variables_hint")}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label={t("wa.variables")}>
              {variables.map((variable) => (
                <button
                  key={variable}
                  type="button"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => insert(variable)}
                  className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700 hover:bg-brand-50 hover:text-brand-700"
                >
                  {`{{${variable}}}`}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div>
        {label(t("wa.footer"), "footer", LIMITS.footer, content.footer.length)}
        <input id="wa-footer" className={box} value={content.footer} onChange={(event) => set({ footer: event.target.value })} />
        {errors.footer ? error("footer") : <p className="mt-1 text-xs text-muted">{t("wa.footer_hint")}</p>}
      </div>

      <div>
        <p className="mb-1.5 text-sm font-medium text-ink">{t("wa.buttons")}</p>
        <div className="space-y-2">
          {content.buttons.map((button, index) => (
            <div key={index} className="grid items-center gap-2 sm:grid-cols-[1fr_11rem_auto]">
              <div>
                <div className="flex items-center gap-2">
                  <input
                    aria-label={t("wa.button_text", { n: index + 1 })}
                    className={box}
                    value={button.text}
                    onChange={(event) =>
                      set({ buttons: content.buttons.map((b, i) => (i === index ? { ...b, text: event.target.value } : b)) })
                    }
                  />
                  <Counter count={button.text.length} max={LIMITS.button} />
                </div>
              </div>
              <SelectField
                label={t("wa.button_outcome")}
                className="sr-only-label"
                value={button.outcome}
                options={(["confirmed", "not_available", "reschedule"] as const).map((o) => ({ value: o, label: t(`outcome.${o}`) }))}
                onChange={(event) =>
                  set({
                    buttons: content.buttons.map((b, i) =>
                      i === index ? { ...b, outcome: event.target.value as typeof b.outcome } : b,
                    ),
                  })
                }
              />
              <Button variant="ghost" onClick={() => set({ buttons: content.buttons.filter((_, i) => i !== index) })}>
                {t("wa.remove")}
              </Button>
            </div>
          ))}
        </div>
        {error("buttons")}
        {content.buttons.length < LIMITS.buttons && !disabled && (
          <Button
            variant="secondary"
            className="mt-2"
            onClick={() => set({ buttons: [...content.buttons, { text: "", outcome: "confirmed" }] })}
          >
            {t("wa.add_button")}
          </Button>
        )}
      </div>

      <div>
        <p className="mb-1.5 text-sm font-medium text-ink">{t("wa.samples")}</p>
        {used.length === 0 ? (
          <p className="text-sm text-muted">{t("wa.no_variables")}</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {used.map((variable) => (
              <label key={variable} className="block">
                <span className="font-mono text-xs text-muted">{`{{${variable}}}`}</span>
                <input
                  aria-label={`${t("wa.samples")}: ${variable}`}
                  className={cn(box, !content.sample_values[variable]?.trim() && !knownSamples[variable] && "ring-amber-400")}
                  placeholder={knownSamples[variable]}
                  value={content.sample_values[variable] ?? ""}
                  onChange={(event) =>
                    set({ sample_values: { ...content.sample_values, [variable]: event.target.value } })
                  }
                />
              </label>
            ))}
          </div>
        )}
        {error("sample_values")}
      </div>
    </fieldset>
  );
}

/** Per-field messages from an API validation error on template content. */
export function contentErrors(fields: Record<string, unknown>): FieldErrors {
  const out: FieldErrors = {};
  for (const key of ["header_text", "body", "footer", "buttons", "sample_values"] as const) {
    const value = fields[key];
    if (value) out[key] = Array.isArray(value) ? value.map(String).join(" ") : JSON.stringify(value);
  }
  return out;
}

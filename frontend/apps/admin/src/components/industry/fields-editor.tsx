"use client";

import { Button, StatusPill, cn } from "@saas/ui";

import { t } from "@/i18n";

import { FIELD_TYPES, type FieldRow, newField } from "./definition";

const cell = "px-2 py-2 align-top";
const input =
  "block w-full rounded-md border-0 px-2 py-1.5 text-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600 disabled:bg-slate-50 disabled:text-slate-500";

/**
 * Field list editor (spec 2.6 / Workflow 3.5): core fields allow label changes only; once clients
 * use the template, types can't change and only optional fields can be added.
 */
export function FieldsEditor({
  fields,
  inUse,
  readOnly,
  onChange,
  label,
}: {
  fields: FieldRow[];
  inUse: boolean;
  readOnly: boolean;
  onChange: (fields: FieldRow[]) => void;
  label: string;
}) {
  const set = (index: number, patch: Partial<FieldRow>) =>
    onChange(fields.map((field, i) => (i === index ? { ...field, ...patch } : field)));

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm" aria-label={label}>
        <thead className="text-left text-xs font-semibold uppercase tracking-wide text-muted">
          <tr>
            <th className={cell}>{t("industry.field.key")}</th>
            <th className={cell}>{t("industry.field.label")}</th>
            <th className={cell}>{t("industry.field.type")}</th>
            <th className={cell}>{t("industry.field.options")}</th>
            <th className={cell}>{t("industry.field.required")}</th>
            <th className={cell} />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {fields.map((field, index) => {
            const lockedType = readOnly || field.is_core || (!field.isNew && inUse);
            const lockedRequired = readOnly || field.is_core || (inUse && !(field.required && !field.isNew));
            return (
              <tr key={index} className={cn(!field.is_active && "opacity-50")} data-field={field.key || `new-${index}`}>
                <td className={cell}>
                  {field.isNew ? (
                    <input
                      aria-label={`${label}: ${t("industry.field.key")} ${index + 1}`}
                      className={cn(input, "font-mono text-xs")}
                      value={field.key}
                      disabled={readOnly}
                      onChange={(event) => set(index, { key: event.target.value.trim() })}
                    />
                  ) : (
                    <span className="font-mono text-xs">{field.key}</span>
                  )}
                </td>
                <td className={cell}>
                  <input
                    aria-label={`${t("industry.field.label")}: ${field.key || index + 1}`}
                    className={input}
                    value={field.label}
                    maxLength={100}
                    disabled={readOnly}
                    onChange={(event) => set(index, { label: event.target.value })}
                  />
                </td>
                <td className={cell}>
                  <select
                    aria-label={`${t("industry.field.type")}: ${field.key || index + 1}`}
                    className={input}
                    value={field.field_type}
                    disabled={lockedType}
                    onChange={(event) => set(index, { field_type: event.target.value as FieldRow["field_type"] })}
                  >
                    {FIELD_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {t(`industry.field_type.${type}`)}
                      </option>
                    ))}
                  </select>
                </td>
                <td className={cell}>
                  {field.field_type === "choice" &&
                    (field.is_core ? (
                      <span className="text-xs text-muted">{field.options.join(", ") || t("common.none")}</span>
                    ) : (
                      <input
                        aria-label={`${t("industry.field.options")}: ${field.key || index + 1}`}
                        className={input}
                        placeholder={t("industry.field.options_hint")}
                        value={field.options.join(", ")}
                        disabled={readOnly}
                        onChange={(event) =>
                          set(index, { options: event.target.value.split(",").map((o) => o.trim()).filter(Boolean) })
                        }
                      />
                    ))}
                </td>
                <td className={cell}>
                  <input
                    type="checkbox"
                    aria-label={`${t("industry.field.required")}: ${field.key || index + 1}`}
                    className="h-4 w-4 rounded border-slate-300 text-brand-600"
                    checked={field.required}
                    disabled={lockedRequired}
                    onChange={(event) => set(index, { required: event.target.checked })}
                  />
                </td>
                <td className={cn(cell, "whitespace-nowrap text-right")}>
                  {field.is_core ? (
                    <StatusPill tone="neutral">{t("industry.field.core")}</StatusPill>
                  ) : readOnly ? null : field.isNew ? (
                    <Button variant="ghost" onClick={() => onChange(fields.filter((_, i) => i !== index))}>
                      {t("industry.field.remove_new")}
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      disabled={!field.is_active && inUse && field.required}
                      onClick={() => set(index, { is_active: !field.is_active })}
                    >
                      {field.is_active ? t("industry.field.switch_off") : t("industry.field.switch_on")}
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!readOnly && (
        <Button variant="secondary" className="mt-3" onClick={() => onChange([...fields, newField()])}>
          {t("industry.field.add")}
        </Button>
      )}
    </div>
  );
}

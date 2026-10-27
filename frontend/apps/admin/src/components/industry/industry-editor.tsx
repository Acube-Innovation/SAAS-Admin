"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, type Column, DataTable, SelectField, Spinner, TextAreaField, TextField, cn } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Tabs } from "@/components/tabs";
import { errorMessage, t } from "@/i18n";
import { api, useMe } from "@/lib/api";
import { errorLines } from "@/lib/api-errors";
import { formatDateTime } from "@/lib/format";
import { useIndustryTemplate, useIndustryVersions } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";
import type { IndustryTemplateVersion } from "@/lib/types";

import {
  type Answer,
  BUTTON_MAX,
  blankDefinition,
  type Definition,
  fromTemplate,
  OUTCOMES,
  type Rule,
  type ScheduleTypeRow,
  toPayload,
} from "./definition";
import { FieldsEditor } from "./fields-editor";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold text-ink">{title}</h2>
      {children}
    </section>
  );
}

/** Industry template editor (spec 2.6). Saving creates a new version (Workflow 3.5). */
export function IndustryEditorPage({ id }: { id: string }) {
  const template = useIndustryTemplate(id);
  const me = useMe();
  const [tab, setTab] = useState<"editor" | "versions">("editor");
  const [savedVersion, setSavedVersion] = useState<number>();
  // Wait for the user too, so a super admin never sees a briefly read-only form.
  if (template.isPending || me.isPending) return <Spinner label={t("app.loading")} />;
  if (template.isError) return <Alert>{errorMessage(template.error)}</Alert>;
  const data = template.data;
  return (
    <>
      <Link href="/industry-templates" className="text-sm font-medium text-brand-600 hover:text-brand-700">
        ← {t("page.industry_templates.title")}
      </Link>
      <div className="mt-3">
        <PageHeader title={data.name} description={`${data.key} · v${data.current_version}`} />
      </div>
      {data.clients_using > 0 && (
        <div className="mb-5">
          <Alert tone="info" title={t("industry.in_use", { count: data.clients_using })}>
            {t("industry.in_use_rules")}
          </Alert>
        </div>
      )}
      <Tabs
        label={data.name}
        active={tab}
        onChange={setTab}
        tabs={[
          { id: "editor", label: t("editor.tab.editor") },
          { id: "versions", label: t("editor.tab.versions") },
        ]}
      />
      <div className="mt-5 space-y-5">
        {savedVersion && tab === "editor" && (
          <Alert tone="success">{t("industry.saved", { version: savedVersion })}</Alert>
        )}
        {tab === "editor" ? (
          // Keyed by version: after a save the form restarts from the saved definition.
          <Editor
            key={data.current_version}
            templateId={id}
            initial={fromTemplate(data)}
            inUse={data.clients_using > 0}
            onSaved={setSavedVersion}
          />
        ) : (
          <Versions id={id} />
        )}
      </div>
    </>
  );
}

export function NewIndustryTemplate() {
  const me = useMe();
  if (me.isPending) return <Spinner label={t("app.loading")} />;
  return (
    <>
      <Link href="/industry-templates" className="text-sm font-medium text-brand-600 hover:text-brand-700">
        ← {t("page.industry_templates.title")}
      </Link>
      <div className="mt-3">
        <PageHeader title={t("industry.new")} />
      </div>
      <Editor initial={blankDefinition()} inUse={false} />
    </>
  );
}

function Editor({
  templateId,
  initial,
  inUse,
  onSaved,
}: {
  templateId?: string;
  initial: Definition;
  inUse: boolean;
  onSaved?: (version: number | undefined) => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useMe();
  const readOnly = !isSuperAdmin(me.data);
  const [definition, setDefinition] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ tone: "error"; lines: string[] }>();
  const creating = !templateId;

  const update = (change: (draft: Definition) => void) =>
    setDefinition((current) => {
      const draft = structuredClone(current);
      change(draft);
      return draft;
    });

  async function save() {
    setSaving(true);
    setResult(undefined);
    onSaved?.(undefined);
    try {
      const body = toPayload(definition) as never;
      const saved = creating
        ? await unwrap(api.POST("/api/admin/industry-templates", { body }))
        : await unwrap(api.PUT("/api/admin/industry-templates/{id}", { params: { path: { id: templateId } }, body }));
      await queryClient.invalidateQueries({ queryKey: ["industry-templates"] });
      if (creating) {
        router.push(`/industry-templates/${saved.id}`);
        return;
      }
      onSaved?.(saved.current_version);
      queryClient.setQueryData(["industry-template", templateId], saved);
      void queryClient.invalidateQueries({ queryKey: ["industry-template", templateId, "versions"] });
    } catch (caught) {
      setResult({ tone: "error", lines: errorLines(caught) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      {result && (
        <Alert tone="error" title={t("editor.errors")}>
          {result.lines.length === 1 ? result.lines[0] : (
            <ul className="list-disc space-y-1 pl-5">{result.lines.map((line) => <li key={line}>{line}</li>)}</ul>
          )}
        </Alert>
      )}
      <Section title={t("industry.general")}>
        <fieldset disabled={readOnly} className="grid gap-4 sm:grid-cols-2">
          <TextField
            label={t("industry.key")}
            hint={t("industry.key_hint")}
            className="font-mono"
            value={definition.key}
            disabled={!creating}
            onChange={(event) => update((d) => void (d.key = event.target.value.trim()))}
          />
          <TextField label={t("industry.name")} value={definition.name} onChange={(event) => update((d) => void (d.name = event.target.value))} />
          <TextAreaField
            label={t("industry.description")}
            value={definition.description}
            maxLength={500}
            className="min-h-16"
            onChange={(event) => update((d) => void (d.description = event.target.value))}
          />
          <label className="flex items-center gap-2 self-center text-sm text-ink">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-slate-300 text-brand-600"
              checked={definition.is_active}
              onChange={(event) => update((d) => void (d.is_active = event.target.checked))}
            />
            {t("industry.active")}
          </label>
        </fieldset>
      </Section>

      <Section title={t("industry.subscriber_fields")}>
        <FieldsEditor
          label={t("industry.subscriber_fields")}
          fields={definition.subscriber_fields}
          inUse={inUse}
          readOnly={readOnly}
          onChange={(fields) => update((d) => void (d.subscriber_fields = fields))}
        />
      </Section>

      {definition.schedule_types.map((scheduleType, index) => (
        <ScheduleTypeEditor
          key={index}
          scheduleType={scheduleType}
          inUse={inUse}
          readOnly={readOnly}
          onChange={(next) => update((d) => void (d.schedule_types[index] = next))}
        />
      ))}

      {!readOnly && (
        <div className="sticky bottom-0 -mx-4 flex justify-end border-t border-slate-200 bg-slate-50/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <Button onClick={save} loading={saving}>
            {creating ? t("industry.create") : t("industry.save")}
          </Button>
        </div>
      )}
    </div>
  );
}

function ScheduleTypeEditor({
  scheduleType,
  inUse,
  readOnly,
  onChange,
}: {
  scheduleType: ScheduleTypeRow;
  inUse: boolean;
  readOnly: boolean;
  onChange: (next: ScheduleTypeRow) => void;
}) {
  const rule = scheduleType.default_reminder_rule;
  const answers = scheduleType.default_question_flow.answers;
  const setRule = (patch: Partial<Rule>) => onChange({ ...scheduleType, default_reminder_rule: { ...rule, ...patch } });
  const setAnswers = (next: Answer[]) => onChange({ ...scheduleType, default_question_flow: { answers: next } });

  return (
    <Section title={`${t("industry.schedule_type")}: ${scheduleType.name || scheduleType.key || "…"}`}>
      <fieldset disabled={readOnly} className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label={t("industry.schedule_type_key")}
            className="font-mono"
            value={scheduleType.key}
            disabled={!scheduleType.isNew}
            onChange={(event) => onChange({ ...scheduleType, key: event.target.value.trim() })}
          />
          <TextField
            label={t("industry.schedule_type_name")}
            value={scheduleType.name}
            onChange={(event) => onChange({ ...scheduleType, name: event.target.value })}
          />
        </div>

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("industry.schedule_fields")}</h3>
          <FieldsEditor
            label={`${scheduleType.name}: ${t("industry.schedule_fields")}`}
            fields={scheduleType.fields}
            inUse={inUse}
            readOnly={readOnly}
            onChange={(fields) => onChange({ ...scheduleType, fields })}
          />
        </div>

        <div>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{t("industry.rule")}</h3>
          <div className="grid gap-4 sm:grid-cols-5">
            <TextField label={t("industry.rule.send_time")} type="time" value={rule.send_time} onChange={(e) => setRule({ send_time: e.target.value })} />
            <TextField label={t("industry.rule.days_before")} type="number" min={0} max={7} value={rule.days_before} onChange={(e) => setRule({ days_before: Number(e.target.value) })} />
            <TextField label={t("industry.rule.wait_minutes")} type="number" min={30} max={1440} value={rule.wait_minutes} onChange={(e) => setRule({ wait_minutes: Number(e.target.value) })} />
            <TextField label={t("industry.rule.quiet_start")} type="time" value={rule.quiet_start} onChange={(e) => setRule({ quiet_start: e.target.value })} />
            <TextField label={t("industry.rule.quiet_end")} type="time" value={rule.quiet_end} onChange={(e) => setRule({ quiet_end: e.target.value })} />
          </div>
          <fieldset className="mt-4">
            <legend className="text-sm font-medium text-ink">{t("industry.rule.weekdays")}</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {[0, 1, 2, 3, 4, 5, 6].map((day) => {
                const on = rule.delivery_weekdays.includes(day);
                return (
                  <label
                    key={day}
                    className={cn(
                      "flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm ring-1 ring-inset",
                      on ? "bg-brand-50 text-brand-700 ring-brand-600/30" : "bg-white text-slate-600 ring-slate-300",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={on}
                      onChange={() =>
                        setRule({
                          delivery_weekdays: on
                            ? rule.delivery_weekdays.filter((d) => d !== day)
                            : [...rule.delivery_weekdays, day].sort(),
                        })
                      }
                    />
                    {t(`weekday.${day}`)}
                  </label>
                );
              })}
            </div>
          </fieldset>
        </div>

        <div>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{t("industry.flow")}</h3>
          <div className="space-y-3">
            {answers.map((answer, index) => {
              const over = answer.label.length > BUTTON_MAX;
              return (
                <div key={index} className="grid items-start gap-3 sm:grid-cols-[1fr_12rem_10rem_auto]">
                  <TextField
                    label={`${t("industry.flow.label")} ${index + 1}`}
                    value={answer.label}
                    error={over ? t("wa.counter", { count: answer.label.length, max: BUTTON_MAX }) : undefined}
                    hint={over ? undefined : t("wa.counter", { count: answer.label.length, max: BUTTON_MAX })}
                    onChange={(e) =>
                      setAnswers(answers.map((a, i) => (i === index ? { ...a, label: e.target.value } : a)))
                    }
                  />
                  <SelectField
                    label={t("industry.flow.outcome")}
                    value={answer.outcome}
                    options={OUTCOMES.map((o) => ({ value: o, label: t(`outcome.${o}`) }))}
                    onChange={(e) => setAnswers(answers.map((a, i) => (i === index ? { ...a, outcome: e.target.value } : a)))}
                  />
                  {answer.outcome === "reschedule" ? (
                    <TextField
                      label={t("industry.flow.pick_days")}
                      type="number"
                      min={1}
                      max={10}
                      value={answer.pick_next_delivery_days ?? 5}
                      onChange={(e) =>
                        setAnswers(answers.map((a, i) => (i === index ? { ...a, pick_next_delivery_days: Number(e.target.value) } : a)))
                      }
                    />
                  ) : (
                    <span />
                  )}
                  {!readOnly && answers.length > 1 && (
                    <Button variant="ghost" className="sm:mt-6" onClick={() => setAnswers(answers.filter((_, i) => i !== index))}>
                      {t("wa.remove")}
                    </Button>
                  )}
                </div>
              );
            })}
            {!readOnly && answers.length < 3 && (
              <Button
                variant="secondary"
                onClick={() => setAnswers([...answers, { key: `answer_${answers.length + 1}`, label: "", outcome: "confirmed" }])}
              >
                {t("industry.flow.add")}
              </Button>
            )}
          </div>
        </div>
      </fieldset>
    </Section>
  );
}

function Versions({ id }: { id: string }) {
  const versions = useIndustryVersions(id);
  const columns: Column<IndustryTemplateVersion>[] = [
    { key: "version", header: t("industry.col.version"), render: (row) => t("industry.version_row", { version: row.version }) },
    { key: "by", header: t("activity.col.actor"), render: (row) => row.created_by_email ?? t("industry.seed") },
    { key: "when", header: t("activity.col.time"), render: (row) => formatDateTime(row.created_at) },
  ];
  return (
    <DataTable
      caption={t("industry.versions")}
      columns={columns}
      rows={versions.data}
      rowKey={(row) => String(row.version)}
      loading={versions.isPending}
      loadingLabel={t("app.loading")}
      empty={t("common.none")}
    />
  );
}

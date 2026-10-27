"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { unwrap } from "@saas/api-client";
import { Alert, Button, SelectField, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { fieldError, t } from "@/i18n";
import { api } from "@/lib/api";
import { settingsSchema } from "@/lib/client-form";
import { keys, useReferenceData } from "@/lib/queries";
import { applyServerErrors } from "@/lib/server-errors";
import type { Tenant } from "@/lib/types";

type Rule = {
  send_time?: string;
  days_before?: number;
  wait_minutes?: number;
  quiet_start?: string;
  quiet_end?: string;
  delivery_weekdays?: number[];
};
type Flow = { answers?: { label: string; outcome: string }[] };
type IndustrySettings = {
  schedule_types?: Record<string, { reminder_rule?: Rule; question_flow?: Flow }>;
  choice_options?: Record<string, string[]>;
};

/** Settings tab: sending settings (super admin edits) and the client's reminder rules (read-only). */
export function SettingsTab({ client, editable }: { client: Tenant; editable: boolean }) {
  const settings = (client.industry_settings ?? {}) as IndustrySettings;
  return (
    <div className="space-y-5">
      <SendingSettings client={client} editable={editable} />
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-ink">{t("settings.reminder_rules")}</h2>
        <p className="mt-1 text-xs text-muted">{t("settings.reminder_rules_hint")}</p>
        {Object.keys(settings.schedule_types ?? {}).length === 0 ? (
          <p className="mt-4 text-sm text-muted">{t("settings.no_template")}</p>
        ) : (
          Object.entries(settings.schedule_types ?? {}).map(([key, value]) => {
            const rule = value.reminder_rule ?? {};
            return (
              <dl key={key} className="mt-4 grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
                <Item label={t("settings.send_time")} value={rule.send_time} />
                <Item label={t("settings.days_before")} value={rule.days_before} />
                <Item label={t("settings.wait")} value={t("settings.wait_minutes", { minutes: rule.wait_minutes ?? 0 })} />
                <Item label={t("settings.quiet_hours")} value={`${rule.quiet_start}–${rule.quiet_end}`} />
                <Item
                  label={t("settings.delivery_days")}
                  value={(rule.delivery_weekdays ?? []).map((d) => t(`weekday.${d}`)).join(", ")}
                />
                <Item
                  label={t("settings.answers")}
                  value={(value.question_flow?.answers ?? []).map((a) => a.label).join(" · ")}
                />
              </dl>
            );
          })
        )}
        {Object.entries(settings.choice_options ?? {}).map(([key, options]) => (
          <div key={key} className="mt-4 text-sm">
            <p className="text-muted">
              {t("settings.choice_lists")}: <span className="font-mono text-xs">{key}</span>
            </p>
            <p className="mt-1 text-ink">{options.join(" · ")}</p>
          </div>
        ))}
      </section>
    </div>
  );
}

function Item({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="mt-0.5 text-ink">{value ?? t("common.none")}</dd>
    </div>
  );
}

function SendingSettings({ client, editable }: { client: Tenant; editable: boolean }) {
  const queryClient = useQueryClient();
  const reference = useReferenceData();
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string }>();
  const form = useForm<z.input<typeof settingsSchema>, unknown, z.output<typeof settingsSchema>>({
    resolver: zodResolver(settingsSchema),
    defaultValues: {
      daily_send_cap: client.daily_send_cap,
      default_language: client.default_language,
      timezone: client.timezone,
    },
  });
  const e = form.formState.errors;

  async function onSubmit(values: z.output<typeof settingsSchema>) {
    setMessage(undefined);
    try {
      const updated = await unwrap(
        api.PATCH("/api/admin/tenants/{id}", { params: { path: { id: client.id } }, body: values }),
      );
      queryClient.setQueryData(keys.tenant(client.id), updated);
      setMessage({ tone: "success", text: t("common.saved") });
    } catch (caught) {
      const text = applyServerErrors(caught, form.setError, ["daily_send_cap", "default_language", "timezone"]);
      if (text) setMessage({ tone: "error", text });
    }
  }

  return (
    <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold text-ink">{t("settings.sending")}</h2>
      {message && (
        <div className="mb-4">
          <Alert tone={message.tone}>{message.text}</Alert>
        </div>
      )}
      <fieldset disabled={!editable} className="grid gap-4 sm:grid-cols-3">
        <TextField
          label={t("field.daily_send_cap")}
          type="number"
          min={1}
          max={100000}
          hint={t("field.daily_send_cap_hint")}
          error={fieldError(e.daily_send_cap)}
          {...form.register("daily_send_cap")}
        />
        <SelectField
          label={t("field.default_language")}
          options={(reference.data?.languages ?? []).map((l) => ({ value: l.code, label: l.name }))}
          error={fieldError(e.default_language)}
          {...form.register("default_language")}
        />
        <SelectField
          label={t("field.timezone")}
          options={(reference.data?.timezones ?? [client.timezone]).map((z) => ({ value: z, label: z }))}
          error={fieldError(e.timezone)}
          {...form.register("timezone")}
        />
      </fieldset>
      {editable && (
        <div className="mt-4 flex justify-end">
          <Button type="submit" loading={form.formState.isSubmitting}>
            {t("common.save")}
          </Button>
        </div>
      )}
    </form>
  );
}

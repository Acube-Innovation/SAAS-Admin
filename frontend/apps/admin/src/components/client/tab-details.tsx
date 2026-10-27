"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { unwrap } from "@saas/api-client";
import { Alert, Button, SelectField, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { fieldError, t } from "@/i18n";
import { api } from "@/lib/api";
import { detailsSchema } from "@/lib/client-form";
import { keys, useReferenceData } from "@/lib/queries";
import { applyServerErrors } from "@/lib/server-errors";
import type { State, Tenant } from "@/lib/types";

import { FormSection } from "./form-section";

const FIELDS = [
  "name", "legal_name", "gstin", "address_line1", "address_line2", "city", "state", "pin_code",
  "contact_name", "contact_phone", "contact_email",
] as const;

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-4 py-2.5 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="col-span-2 text-ink">{value || t("common.none")}</dd>
    </div>
  );
}

/** Details tab: view; super admins edit (same rules as the create form). */
export function DetailsTab({ client, editable }: { client: Tenant; editable: boolean }) {
  const reference = useReferenceData();
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const states = (reference.data?.states ?? []) as State[];
  const stateName = states.find((s) => s.code === client.state)?.name ?? client.state;

  if (editing && reference.data) {
    return (
      <DetailsForm
        client={client}
        states={states}
        onDone={(didSave) => {
          setEditing(false);
          setSaved(didSave);
        }}
      />
    );
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">{t("details.edit_title")}</h2>
        {editable && (
          <Button variant="secondary" onClick={() => setEditing(true)}>
            {t("common.edit")}
          </Button>
        )}
      </div>
      {saved && <Alert tone="success">{t("common.saved")}</Alert>}
      <dl className="divide-y divide-slate-100">
        <Row label={t("field.name")} value={client.name} />
        <Row label={t("field.legal_name")} value={client.legal_name} />
        <Row label={t("field.gstin")} value={client.gstin} />
        <Row
          label={t("client_form.section.address")}
          value={[client.address_line1, client.address_line2, client.city, `${stateName} ${client.pin_code}`]
            .filter(Boolean)
            .join(", ")}
        />
        <Row label={t("field.contact_name")} value={client.contact_name} />
        <Row label={t("field.contact_phone")} value={client.contact_phone} />
        <Row label={t("field.contact_email")} value={client.contact_email} />
        <Row label={t("client_form.industry_template")} value={client.industry_template_name} />
      </dl>
    </div>
  );
}

function DetailsForm({ client, states, onDone }: { client: Tenant; states: State[]; onDone: (saved: boolean) => void }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string>();
  const schema = useMemo(() => detailsSchema(Object.fromEntries(states.map((s) => [s.code, s.gst_code]))), [states]);
  type Schema = typeof schema;
  const form = useForm<z.input<Schema>, unknown, z.output<Schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: client.name, legal_name: client.legal_name, gstin: client.gstin ?? "",
      address_line1: client.address_line1, address_line2: client.address_line2 ?? "", city: client.city,
      state: client.state, pin_code: client.pin_code, contact_name: client.contact_name,
      contact_phone: client.contact_phone, contact_email: client.contact_email,
    },
  });
  const e = form.formState.errors;
  const reg = form.register;

  async function onSubmit(values: z.output<Schema>) {
    setError(undefined);
    try {
      const updated = await unwrap(
        api.PATCH("/api/admin/tenants/{id}", {
          params: { path: { id: client.id } },
          body: { ...values, gstin: values.gstin || null } as never,
        }),
      );
      queryClient.setQueryData(keys.tenant(client.id), updated);
      void queryClient.invalidateQueries({ queryKey: ["tenants"] });
      onDone(true);
    } catch (caught) {
      setError(applyServerErrors(caught, form.setError, FIELDS));
    }
  }

  return (
    <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
      {error && <Alert>{error}</Alert>}
      <FormSection title={t("details.edit_title")}>
        <TextField label={t("field.name")} required error={fieldError(e.name)} {...reg("name")} />
        <TextField label={t("field.legal_name")} required error={fieldError(e.legal_name)} {...reg("legal_name")} />
        <TextField label={t("field.gstin")} hint={t("field.gstin_hint")} className="uppercase" error={fieldError(e.gstin)} {...reg("gstin")} />
        <TextField label={t("field.address_line1")} required error={fieldError(e.address_line1)} {...reg("address_line1")} />
        <TextField label={t("field.address_line2")} error={fieldError(e.address_line2)} {...reg("address_line2")} />
        <TextField label={t("field.city")} required error={fieldError(e.city)} {...reg("city")} />
        <SelectField label={t("field.state")} required options={states.map((s) => ({ value: s.code, label: s.name }))} error={fieldError(e.state)} {...reg("state")} />
        <TextField label={t("field.pin_code")} required inputMode="numeric" maxLength={6} error={fieldError(e.pin_code)} {...reg("pin_code")} />
        <TextField label={t("field.contact_name")} required error={fieldError(e.contact_name)} {...reg("contact_name")} />
        <TextField label={t("field.contact_phone")} required type="tel" error={fieldError(e.contact_phone)} {...reg("contact_phone")} />
        <TextField label={t("field.contact_email")} required type="email" error={fieldError(e.contact_email)} {...reg("contact_email")} />
      </FormSection>
      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={() => onDone(false)}>
          {t("common.cancel")}
        </Button>
        <Button type="submit" loading={form.formState.isSubmitting}>
          {t("common.save")}
        </Button>
      </div>
    </form>
  );
}

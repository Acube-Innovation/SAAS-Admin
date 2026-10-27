"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { unwrap } from "@saas/api-client";
import { Alert, Button, EmptyState, SelectField, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { PageHeader } from "@/components/page-header";
import { fieldError, t } from "@/i18n";
import { api, useMe } from "@/lib/api";
import { createClientSchema } from "@/lib/client-form";
import { useIndustryTemplates, useReferenceData } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";
import { applyServerErrors } from "@/lib/server-errors";

import { FormSection } from "./form-section";

type Schema = ReturnType<typeof createClientSchema>;

const FIELDS = [
  "industry_template", "name", "legal_name", "gstin", "address_line1", "address_line2", "city", "state",
  "pin_code", "contact_name", "contact_phone", "contact_email", "default_language", "daily_send_cap",
  "owner_name", "owner_email", "owner_phone",
] as const;

/** Create client (spec 2.4, Workflow 3.1): tenant + owner invitation in one step. */
export function CreateClientForm() {
  const me = useMe();
  const reference = useReferenceData();
  const industries = useIndustryTemplates();
  if (me.data && !isSuperAdmin(me.data)) {
    return <EmptyState title={t("denied.title")} description={t("denied.body")} />;
  }
  if (!reference.data || !industries.data) return null;
  return (
    <Form
      states={reference.data.states}
      languages={reference.data.languages}
      industries={industries.data.filter((tpl) => tpl.is_active)}
    />
  );
}

function Form({
  states,
  languages,
  industries,
}: {
  states: { code: string; name: string; gst_code: string }[];
  languages: { code: string; name: string }[];
  industries: { id: string; name: string }[];
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string>();
  const schema = useMemo(
    () => createClientSchema(Object.fromEntries(states.map((s) => [s.code, s.gst_code]))),
    [states],
  );
  const form = useForm<z.input<Schema>, unknown, z.output<Schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      industry_template: industries.length === 1 ? industries[0]!.id : "",
      name: "", legal_name: "", gstin: "", address_line1: "", address_line2: "", city: "", state: "",
      pin_code: "", contact_name: "", contact_phone: "", contact_email: "", default_language: "ml",
      daily_send_cap: 5000, owner_name: "", owner_email: "", owner_phone: "",
    },
  });
  const e = form.formState.errors;
  const reg = form.register;

  async function onSubmit(values: z.output<Schema>) {
    setError(undefined);
    try {
      const created = await unwrap(
        api.POST("/api/admin/tenants", {
          body: { ...values, gstin: values.gstin || null, state: values.state as never } as never,
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ["tenants"] });
      router.push(`/clients/${created.id}?tab=users&created=1`);
    } catch (caught) {
      setError(applyServerErrors(caught, form.setError, FIELDS));
    }
  }

  return (
    <>
      <Link href="/clients" className="text-sm font-medium text-brand-600 hover:text-brand-700">
        ← {t("page.clients.title")}
      </Link>
      <div className="mt-3">
        <PageHeader title={t("client_form.title")} description={t("client_form.description")} />
      </div>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
        {error && <Alert>{error}</Alert>}
        <FormSection title={t("client_form.section.client")}>
          <SelectField
            label={t("client_form.industry_template")}
            required
            placeholder={t("client_form.choose")}
            options={industries.map((tpl) => ({ value: tpl.id, label: tpl.name }))}
            error={fieldError(e.industry_template)}
            {...reg("industry_template")}
          />
          <TextField label={t("field.name")} required error={fieldError(e.name)} {...reg("name")} />
          <TextField label={t("field.legal_name")} required error={fieldError(e.legal_name)} {...reg("legal_name")} />
          <TextField
            label={t("field.gstin")}
            hint={t("field.gstin_hint")}
            className="uppercase"
            error={fieldError(e.gstin)}
            {...reg("gstin")}
          />
        </FormSection>
        <FormSection title={t("client_form.section.address")}>
          <TextField label={t("field.address_line1")} required error={fieldError(e.address_line1)} {...reg("address_line1")} />
          <TextField label={t("field.address_line2")} error={fieldError(e.address_line2)} {...reg("address_line2")} />
          <TextField label={t("field.city")} required error={fieldError(e.city)} {...reg("city")} />
          <SelectField
            label={t("field.state")}
            required
            placeholder={t("client_form.choose")}
            options={states.map((s) => ({ value: s.code, label: s.name }))}
            error={fieldError(e.state)}
            {...reg("state")}
          />
          <TextField label={t("field.pin_code")} required inputMode="numeric" maxLength={6} error={fieldError(e.pin_code)} {...reg("pin_code")} />
        </FormSection>
        <FormSection title={t("client_form.section.contact")}>
          <TextField label={t("field.contact_name")} required error={fieldError(e.contact_name)} {...reg("contact_name")} />
          <TextField label={t("field.contact_phone")} required type="tel" hint={t("field.phone_hint")} error={fieldError(e.contact_phone)} {...reg("contact_phone")} />
          <TextField label={t("field.contact_email")} required type="email" error={fieldError(e.contact_email)} {...reg("contact_email")} />
        </FormSection>
        <FormSection title={t("client_form.section.settings")}>
          <SelectField
            label={t("field.default_language")}
            required
            options={languages.map((l) => ({ value: l.code, label: l.name }))}
            error={fieldError(e.default_language)}
            {...reg("default_language")}
          />
          <TextField
            label={t("field.daily_send_cap")}
            required
            type="number"
            min={1}
            max={100000}
            hint={t("field.daily_send_cap_hint")}
            error={fieldError(e.daily_send_cap)}
            {...reg("daily_send_cap")}
          />
        </FormSection>
        <FormSection title={t("client_form.section.owner")}>
          <TextField label={t("field.owner_name")} required error={fieldError(e.owner_name)} {...reg("owner_name")} />
          <TextField label={t("field.owner_email")} required type="email" error={fieldError(e.owner_email)} {...reg("owner_email")} />
          <TextField label={t("field.owner_phone")} type="tel" hint={t("field.phone_hint")} error={fieldError(e.owner_phone)} {...reg("owner_phone")} />
        </FormSection>
        <div className="flex justify-end gap-3">
          <Link href="/clients">
            <Button variant="secondary">{t("common.cancel")}</Button>
          </Link>
          <Button type="submit" loading={form.formState.isSubmitting}>
            {t("client_form.submit")}
          </Button>
        </div>
      </form>
    </>
  );
}

"use client";

import { ApiError, unwrap } from "@saas/api-client";
import { Alert, Button, SelectField, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { errorMessage, t } from "@/i18n";
import { api } from "@/lib/api";
import { useIndustryTemplate, useReferenceData, useTemplateVariables, useTenants } from "@/lib/queries";

import { emptyContent, withKnownSamples } from "./content";
import { ContentEditor, contentErrors } from "./content-editor";
import { WhatsAppPreview } from "./preview";

/** New WhatsApp template: choose the client, name, language and schedule type; v1 is a draft. */
export function NewWhatsAppTemplate() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const reference = useReferenceData();
  const [clientSearch, setClientSearch] = useState("");
  const clients = useTenants({ search: clientSearch });
  const [tenantId, setTenantId] = useState(params.get("tenant") ?? "");
  const client = clients.data?.results.find((c) => c.id === tenantId);
  const industry = useIndustryTemplate(client?.industry_template ?? "");
  const scheduleTypes = (industry.data?.schedule_types ?? []).filter((st) => st.is_active);
  const [scheduleType, setScheduleType] = useState("");
  const selectedSt = scheduleType || scheduleTypes[0]?.id || "";
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en");
  const [content, setContent] = useState(emptyContent());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const variables = useTemplateVariables(selectedSt || undefined);

  async function create() {
    setBusy(true);
    setError(undefined);
    setErrors({});
    try {
      const created = await unwrap(
        api.POST("/api/admin/whatsapp-templates", {
          body: {
            tenant: tenantId,
            name,
            language: language as never,
            schedule_type: selectedSt,
            ...withKnownSamples(content, client?.name ?? ""),
          } as never,
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ["whatsapp-templates"] });
      router.push(`/whatsapp-templates/${created.id}`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "validation_error") {
        const fields = caught.fields as Record<string, unknown>;
        setErrors({
          ...contentErrors(fields),
          ...Object.fromEntries(
            ["tenant", "name", "language", "schedule_type"].filter((k) => fields[k]).map((k) => [k, String(fields[k])]),
          ),
        });
      }
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Link href="/whatsapp-templates" className="text-sm font-medium text-brand-600 hover:text-brand-700">
        ← {t("page.whatsapp_templates.title")}
      </Link>
      <div className="mt-3">
        <PageHeader title={t("wa.new")} />
      </div>
      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-5">
          <section className="grid gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:grid-cols-2">
            <TextField label={t("wa.client_search")} type="search" value={clientSearch} onChange={(e) => setClientSearch(e.target.value)} />
            <SelectField
              label={t("wa.client")}
              required
              value={tenantId}
              placeholder={t("client_form.choose")}
              options={(clients.data?.results ?? [])
                .filter((c) => c.industry_template)
                .map((c) => ({ value: c.id, label: c.name }))}
              error={errors.tenant}
              onChange={(e) => {
                setTenantId(e.target.value);
                setScheduleType("");
              }}
            />
            <TextField
              label={t("wa.name")}
              required
              hint={t("wa.name_hint")}
              className="font-mono"
              value={name}
              error={errors.name}
              onChange={(e) => setName(e.target.value)}
            />
            <SelectField
              label={t("wa.language")}
              required
              value={language}
              options={(reference.data?.languages ?? []).map((l) => ({ value: l.code, label: l.name }))}
              onChange={(e) => setLanguage(e.target.value)}
            />
            <SelectField
              label={t("wa.schedule_type")}
              required
              value={selectedSt}
              options={scheduleTypes.map((st) => ({ value: st.id, label: st.name }))}
              error={errors.schedule_type}
              onChange={(e) => setScheduleType(e.target.value)}
            />
          </section>
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <ContentEditor
              content={content}
              onChange={setContent}
              variables={variables.data?.variables ?? []}
              errors={errors}
              knownSamples={client ? { agency_name: client.name } : {}}
            />
          </section>
          <div className="flex justify-end">
            <Button loading={busy} disabled={!tenantId || !name || !selectedSt} onClick={create}>
              {t("wa.create")}
            </Button>
          </div>
        </div>
        <aside>
          <WhatsAppPreview content={content} agencyName={client?.name ?? "Agency"} />
        </aside>
      </div>
    </>
  );
}

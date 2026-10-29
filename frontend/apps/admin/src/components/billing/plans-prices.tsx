"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, Dialog, SelectField, Spinner, StatusBadge, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { errorMessage, t } from "@/i18n";
import { api, ensureCsrf, useMe } from "@/lib/api";
import { formatDate, formatRupees, istToday } from "@/lib/format";
import { keys, usePlans, usePriceList } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";
import type { PlanAdmin } from "@/lib/types";

export const CHANNELS = ["whatsapp_utility", "whatsapp_service", "voice_minute"] as const;
const FEATURES = ["voice_ai", "public_api", "staff_app"] as const;

/** Plans and price list (spec 2.11). Super admins change them; support staff view. */
export function PlansAndPrices() {
  const me = useMe();
  const superAdmin = isSuperAdmin(me.data);
  return (
    <>
      <PageHeader title={t("page.billing.title")} description={t("page.billing.description")} />
      <div className="space-y-8">
        <PlansSection superAdmin={superAdmin} />
        <PriceListSection superAdmin={superAdmin} />
      </div>
    </>
  );
}

type PlanForm = {
  code: string;
  name: string;
  fee: string;
  max: string;
  features: Record<(typeof FEATURES)[number], boolean>;
  is_active: boolean;
};

const emptyPlan: PlanForm = { code: "", name: "", fee: "", max: "", features: { voice_ai: false, public_api: false, staff_app: false }, is_active: true };

function PlansSection({ superAdmin }: { superAdmin: boolean }) {
  const plans = usePlans();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<PlanAdmin | "new" | null>(null);
  const [form, setForm] = useState<PlanForm>(emptyPlan);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string>();

  function open(plan: PlanAdmin | "new") {
    setError(undefined);
    setEditing(plan);
    setForm(
      plan === "new"
        ? emptyPlan
        : {
            code: plan.code,
            name: plan.name,
            fee: String(plan.monthly_fee_paise / 100),
            max: String(plan.max_active_subscribers),
            features: { voice_ai: plan.features.voice_ai ?? false, public_api: plan.features.public_api ?? false, staff_app: plan.features.staff_app ?? false },
            is_active: plan.is_active,
          },
    );
  }

  async function save() {
    const fee = Number(form.fee);
    const max = Number(form.max);
    if (!form.name.trim() || form.name.trim().length < 2) return setError(t("billing.plan_name_error"));
    if (!Number.isFinite(fee) || fee < 0 || Math.round(fee * 100) !== fee * 100) return setError(t("billing.plan_fee_error"));
    if (!Number.isInteger(max) || max < 1) return setError(t("billing.plan_max_error"));
    if (editing === "new" && !/^[a-z0-9-]{2,30}$/.test(form.code)) return setError(t("billing.plan_code_error"));
    const body = { name: form.name.trim(), monthly_fee_paise: Math.round(fee * 100), max_active_subscribers: max, features: form.features, is_active: form.is_active };
    setBusy(true);
    setError(undefined);
    try {
      await ensureCsrf();
      if (editing === "new") await unwrap(api.POST("/api/admin/plans", { body: { ...body, code: form.code } as never }));
      else if (editing) await unwrap(api.PATCH("/api/admin/plans/{id}", { params: { path: { id: editing.id } }, body: body as never }));
      await queryClient.invalidateQueries({ queryKey: keys.plans });
      setNotice(t(editing === "new" ? "billing.plan_added" : "billing.plan_saved"));
      setEditing(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label={t("billing.plans")} className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-ink">{t("billing.plans")}</h2>
        {superAdmin && <Button onClick={() => open("new")}>{t("billing.add_plan")}</Button>}
      </div>
      {notice && <Alert tone="success">{notice}</Alert>}
      {plans.isError && <Alert>{errorMessage(plans.error)}</Alert>}
      {plans.isPending ? (
        <Spinner label={t("app.loading")} />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <caption className="sr-only">{t("billing.plans")}</caption>
            <thead className="bg-slate-50 text-left text-xs uppercase text-muted">
              <tr>
                <th scope="col" className="px-4 py-2">{t("billing.col.plan")}</th>
                <th scope="col" className="px-4 py-2 text-right">{t("billing.col.fee")}</th>
                <th scope="col" className="px-4 py-2 text-right">{t("billing.col.max")}</th>
                <th scope="col" className="px-4 py-2">{t("billing.col.features")}</th>
                <th scope="col" className="px-4 py-2">{t("billing.col.status")}</th>
                <th scope="col" className="px-4 py-2"><span className="sr-only">{t("common.edit")}</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {(plans.data ?? []).map((plan) => (
                <tr key={plan.id} data-plan={plan.code}>
                  <td className="px-4 py-2">
                    <span className="font-medium text-ink">{plan.name}</span>
                    <span className="block font-mono text-xs text-muted">{plan.code}</span>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{formatRupees(plan.monthly_fee_paise)}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{plan.max_active_subscribers.toLocaleString("en-IN")}</td>
                  <td className="px-4 py-2 text-muted">
                    {FEATURES.filter((f) => plan.features[f]).map((f) => t(`billing.feature.${f}`)).join(", ") || t("common.none")}
                  </td>
                  <td className="px-4 py-2">
                    <span className="flex flex-wrap gap-1">
                      <StatusBadge status={plan.is_active ? "active" : "disabled"} label={t(plan.is_active ? "billing.active" : "billing.retired")} />
                      {plan.is_placeholder && <StatusBadge status="draft" label={t("billing.placeholder")} />}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {superAdmin && <Button variant="secondary" onClick={() => open(plan)}>{t("common.edit")}</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-muted">{t("billing.plans_hint")}</p>
      <Dialog
        open={editing !== null}
        title={editing === "new" ? t("billing.add_plan") : t("billing.edit_plan", { plan: editing?.name ?? "" })}
        onClose={() => setEditing(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)}>{t("common.cancel")}</Button>
            <Button onClick={save} loading={busy}>{t("common.save")}</Button>
          </>
        }
      >
        <div className="space-y-3">
          {error && <Alert>{error}</Alert>}
          {editing === "new" ? (
            <TextField label={t("billing.code")} hint={t("billing.code_hint")} required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
          ) : (
            <p className="text-xs text-muted">{t("billing.code_fixed", { code: form.code })}</p>
          )}
          <TextField label={t("billing.name")} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <div className="grid grid-cols-2 gap-3">
            <TextField label={t("billing.fee")} type="number" min={0} step="0.01" required value={form.fee} onChange={(e) => setForm({ ...form, fee: e.target.value })} />
            <TextField label={t("billing.max")} type="number" min={1} required value={form.max} onChange={(e) => setForm({ ...form, max: e.target.value })} />
          </div>
          <fieldset className="space-y-1">
            <legend className="text-sm font-medium text-ink">{t("billing.col.features")}</legend>
            {FEATURES.map((f) => (
              <label key={f} className="flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" className="h-4 w-4" checked={form.features[f]} onChange={(e) => setForm({ ...form, features: { ...form.features, [f]: e.target.checked } })} />
                {t(`billing.feature.${f}`)}
              </label>
            ))}
          </fieldset>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" className="h-4 w-4" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
            {t("billing.active_hint")}
          </label>
        </div>
      </Dialog>
    </section>
  );
}

function PriceListSection({ superAdmin }: { superAdmin: boolean }) {
  const prices = usePriceList();
  const queryClient = useQueryClient();
  const [channel, setChannel] = useState<string>("whatsapp_utility");
  const [rupees, setRupees] = useState("");
  const [from, setFrom] = useState(istToday());
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [busy, setBusy] = useState(false);
  const today = istToday();

  async function run(call: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await ensureCsrf();
      await call();
      await queryClient.invalidateQueries({ queryKey: keys.priceList });
      setNotice(message);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const paise = Math.round(Number(rupees) * 100);
    if (!Number.isFinite(paise) || paise < 1) return setError(t("billing.price_error"));
    if (from < today) return setError(t("billing.no_backdating"));
    await run(() => unwrap(api.POST("/api/admin/price-list", { body: { channel: channel as never, price_paise: paise, effective_from: from } })), t("billing.price_added"));
    setRupees("");
  }

  return (
    <section aria-label={t("billing.price_list")} className="space-y-3">
      <h2 className="text-base font-semibold text-ink">{t("billing.price_list")}</h2>
      {prices.isError && <Alert>{errorMessage(prices.error)}</Alert>}
      {!prices.data ? (
        prices.isPending && <Spinner label={t("app.loading")} />
      ) : (
        <>
          <dl className="grid gap-3 sm:grid-cols-3" aria-label={t("billing.current_prices")}>
            {prices.data.current.map((price) => (
              <div key={price.channel} className="rounded-xl border border-slate-200 bg-white p-4" data-channel={price.channel}>
                <dt className="text-sm text-muted">{t(`billing.channel.${price.channel}`)}</dt>
                <dd className="mt-1 text-2xl font-semibold tabular-nums text-ink">{formatRupees(price.price_paise)}</dd>
              </div>
            ))}
          </dl>
          {superAdmin && (
            <div className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-[14rem_9rem_11rem_auto] sm:items-end">
              <SelectField label={t("billing.channel")} value={channel} options={CHANNELS.map((c) => ({ value: c, label: t(`billing.channel.${c}`) }))} onChange={(e) => setChannel(e.target.value)} />
              <TextField label={t("billing.price")} type="number" min={0.01} step="0.01" value={rupees} onChange={(e) => setRupees(e.target.value)} />
              <TextField label={t("billing.effective_from")} type="date" min={today} value={from} onChange={(e) => setFrom(e.target.value)} />
              <Button onClick={add} loading={busy}>{t("billing.add_price")}</Button>
            </div>
          )}
          {error && <Alert>{error}</Alert>}
          {notice && <Alert tone="success">{notice}</Alert>}
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <caption className="sr-only">{t("billing.price_history")}</caption>
              <thead className="bg-slate-50 text-left text-xs uppercase text-muted">
                <tr>
                  <th scope="col" className="px-4 py-2">{t("billing.channel")}</th>
                  <th scope="col" className="px-4 py-2 text-right">{t("billing.price")}</th>
                  <th scope="col" className="px-4 py-2">{t("billing.effective_from")}</th>
                  <th scope="col" className="px-4 py-2"><span className="sr-only">{t("billing.delete")}</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {prices.data.entries.map((entry) => (
                  <tr key={entry.id} data-entry={entry.id}>
                    <td className="px-4 py-2 text-ink">{t(`billing.channel.${entry.channel}`)}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{formatRupees(entry.price_paise)}</td>
                    <td className="px-4 py-2 text-muted">
                      {formatDate(entry.effective_from)}
                      {entry.effective_from > today && <span className="ml-2 text-xs text-sky-700">{t("billing.scheduled")}</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {superAdmin && entry.effective_from > today && (
                        <Button variant="ghost" onClick={() => run(() => unwrap(api.DELETE("/api/admin/price-list/{id}", { params: { path: { id: entry.id } } })), t("billing.price_deleted"))}>
                          {t("billing.delete")}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted">{t("billing.price_hint")}</p>
        </>
      )}
    </section>
  );
}

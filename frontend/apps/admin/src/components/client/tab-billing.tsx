"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, ConfirmDialog, SelectField, Spinner, StatusBadge, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { CHANNELS } from "@/components/billing/plans-prices";
import { errorMessage, t } from "@/i18n";
import { api, ensureCsrf, useMe } from "@/lib/api";
import { formatDate, formatDateTime, formatRupees, istToday } from "@/lib/format";
import { keys, usePlans, useTenantBilling, useTenantOverrides } from "@/lib/queries";
import { isSuperAdmin } from "@/lib/roles";

/** Billing tab (spec 2.5): plan and pending change (3.8), prices and overrides, read-only wallet. */
export function BillingTab({ tenantId }: { tenantId: string }) {
  const billing = useTenantBilling(tenantId);
  const me = useMe();
  const superAdmin = isSuperAdmin(me.data);
  if (billing.isPending) return <Spinner label={t("app.loading")} />;
  if (billing.isError) return <Alert>{errorMessage(billing.error)}</Alert>;
  const data = billing.data;
  return (
    <div className="space-y-6">
      <PlanCard tenantId={tenantId} superAdmin={superAdmin} />
      <section aria-label={t("billing.client_prices")} className="space-y-3">
        <h3 className="text-sm font-semibold text-ink">{t("billing.client_prices")}</h3>
        <dl className="grid gap-3 sm:grid-cols-3">
          {data.prices.map((price) => (
            <div key={price.channel} className="rounded-xl border border-slate-200 bg-white p-4" data-channel={price.channel} data-source={price.source}>
              <dt className="text-sm text-muted">{t(`billing.channel.${price.channel}`)}</dt>
              <dd className="mt-1 text-xl font-semibold tabular-nums text-ink">{formatRupees(price.price_paise)}</dd>
              <dd className="text-xs text-muted">{t(`billing.source.${price.source}`)}</dd>
            </div>
          ))}
        </dl>
        <Overrides tenantId={tenantId} superAdmin={superAdmin} />
      </section>
      <section aria-label={t("billing.wallet")} className="space-y-3">
        <h3 className="text-sm font-semibold text-ink">{t("billing.wallet")} <span className="font-normal text-muted">· {t("billing.read_only")}</span></h3>
        <div className="grid gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-sm text-muted">{t("billing.balance")}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-ink" data-testid="admin-wallet-balance">{formatRupees(data.wallet.balance_paise)}</p>
            <p className="text-xs text-muted">{t("billing.low_level", { amount: formatRupees(data.wallet.low_balance_level_paise) })}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-sm text-muted">{t("billing.sending")}</p>
            <p className="mt-2">
              <StatusBadge
                status={data.wallet.can_send ? (data.wallet.is_low ? "paused" : "active") : "suspended"}
                label={t(data.wallet.can_send ? (data.wallet.is_low ? "billing.low" : "billing.sending_ok") : "billing.paused")}
              />
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-sm text-muted">{t("billing.auto_top_up")}</p>
            <p className="mt-1 font-medium text-ink">
              {data.mandate ? t("billing.mandate", { status: t(`billing.mandate_status.${data.mandate.status}`), amount: formatRupees(data.mandate.credit_paise) }) : t("billing.no_mandate")}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-sm text-muted">{t("billing.active_subscribers")}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-ink">{data.active_subscribers.toLocaleString("en-IN")}</p>
          </div>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <caption className="px-4 pt-3 text-left text-xs font-semibold uppercase text-muted">{t("billing.recent_transactions")}</caption>
              <tbody className="divide-y divide-slate-100">
                {data.recent_transactions.length === 0 && <tr><td className="px-4 py-3 text-muted">{t("common.none")}</td></tr>}
                {data.recent_transactions.map((row) => (
                  <tr key={row.id}>
                    <td className="px-4 py-2 text-muted">{formatDateTime(row.created_at)}</td>
                    <td className="px-4 py-2 text-ink">{t(`billing.reason.${row.reason}`)}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{row.kind === "credit" ? "+" : "−"}{formatRupees(row.amount_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <caption className="px-4 pt-3 text-left text-xs font-semibold uppercase text-muted">{t("billing.invoices")}</caption>
              <tbody className="divide-y divide-slate-100">
                {data.invoices.length === 0 && <tr><td className="px-4 py-3 text-muted">{t("common.none")}</td></tr>}
                {data.invoices.map((invoice) => (
                  <tr key={invoice.id}>
                    <td className="px-4 py-2 font-mono text-xs text-ink">{invoice.number}</td>
                    <td className="px-4 py-2 text-muted">{formatDate(invoice.issued_at)}</td>
                    <td className="px-4 py-2 text-ink">{t(`billing.invoice_kind.${invoice.kind}`)}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{formatRupees(invoice.total_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}

function PlanCard({ tenantId, superAdmin }: { tenantId: string; superAdmin: boolean }) {
  const billing = useTenantBilling(tenantId);
  const plans = usePlans();
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [confirm, setConfirm] = useState(false);
  const sub = billing.data?.subscription;
  if (!sub) return null;
  const options = (plans.data ?? []).filter((p) => p.is_active && p.code !== sub.plan?.code);
  const chosen = options.find((p) => p.code === choice);

  async function run(call: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await ensureCsrf();
      await call();
      await queryClient.invalidateQueries({ queryKey: keys.tenantBilling(tenantId) });
      setNotice(message);
      return true;
    } catch (caught) {
      setError(errorMessage(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }

  const path = { params: { path: { id: tenantId } } };
  return (
    <section aria-label={t("billing.plan")} className="rounded-xl border border-slate-200 bg-white p-5" data-plan={sub.plan?.code}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">{t("billing.plan")}</p>
          <h3 className="text-lg font-semibold text-ink">{sub.plan?.name ?? t("common.none")}</h3>
        </div>
        {sub.plan && <p className="text-lg font-semibold tabular-nums text-ink">{t("billing.per_month", { amount: formatRupees(sub.plan.monthly_fee_paise) })}</p>}
      </div>
      <p className="mt-2 text-sm text-muted">
        {t("billing.period", { start: formatDate(sub.current_period_start), next: formatDate(sub.next_billing_date) })}
      </p>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
      {notice && <div className="mt-3"><Alert tone="success">{notice}</Alert></div>}
      {sub.pending_plan && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-amber-50 p-3 text-sm" data-pending={sub.pending_plan.code}>
          <span className="text-ink">{t("billing.pending", { plan: sub.pending_plan.name, date: formatDate(sub.next_billing_date) })}</span>
          {superAdmin && (
            <Button variant="secondary" loading={busy} onClick={() => run(() => unwrap(api.DELETE("/api/admin/tenants/{id}/subscription/plan-change", path)), t("billing.change_cancelled"))}>
              {t("billing.cancel_change")}
            </Button>
          )}
        </div>
      )}
      {superAdmin && !sub.pending_plan && (
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="w-64">
            <SelectField label={t("billing.change_to")} value={choice} placeholder={t("billing.choose")} options={options.map((p) => ({ value: p.code, label: `${p.name} · ${formatRupees(p.monthly_fee_paise)}` }))} onChange={(e) => setChoice(e.target.value)} />
          </div>
          <Button disabled={!chosen} onClick={() => setConfirm(true)}>{t("billing.schedule_change")}</Button>
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        title={t("billing.confirm_title", { plan: chosen?.name ?? "" })}
        message={t("billing.confirm_message", { date: formatDate(sub.next_billing_date) })}
        confirmLabel={t("billing.schedule_change")}
        cancelLabel={t("common.cancel")}
        busy={busy}
        onConfirm={async () => {
          if (await run(() => unwrap(api.POST("/api/admin/tenants/{id}/subscription/plan-change", { ...path, body: { plan_code: choice } })), t("billing.change_scheduled", { date: formatDate(sub.next_billing_date) }))) {
            setConfirm(false);
            setChoice("");
          }
        }}
        onClose={() => setConfirm(false)}
      />
    </section>
  );
}

function Overrides({ tenantId, superAdmin }: { tenantId: string; superAdmin: boolean }) {
  const overrides = useTenantOverrides(tenantId);
  const queryClient = useQueryClient();
  const today = istToday();
  const [channel, setChannel] = useState<string>("whatsapp_utility");
  const [rupees, setRupees] = useState("");
  const [starts, setStarts] = useState(today);
  const [ends, setEnds] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  async function run(call: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await ensureCsrf();
      await call();
      await queryClient.invalidateQueries({ queryKey: keys.tenantBilling(tenantId) });
      setNotice(message);
      return true;
    } catch (caught) {
      setError(errorMessage(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const paise = Math.round(Number(rupees) * 100);
    if (!rupees || !Number.isFinite(paise) || paise < 0) return setError(t("billing.price_error"));
    if (starts < today) return setError(t("billing.no_backdating"));
    if (ends && ends < starts) return setError(t("billing.ends_before_start"));
    const ok = await run(
      () => unwrap(api.POST("/api/admin/tenants/{id}/price-overrides", { params: { path: { id: tenantId } }, body: { channel: channel as never, price_paise: paise, starts_on: starts, ends_on: ends || null } })),
      t("billing.override_added"),
    );
    if (ok) {
      setRupees("");
      setEnds("");
    }
  }

  const overridePath = (overrideId: string) => ({ params: { path: { id: tenantId, override_id: overrideId } } });
  return (
    <div className="space-y-3" aria-label={t("billing.overrides")} role="group">
      <h4 className="text-sm font-medium text-ink">{t("billing.overrides")}</h4>
      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      {overrides.isPending ? (
        <Spinner label={t("app.loading")} />
      ) : (overrides.data ?? []).length === 0 ? (
        <p className="text-sm text-muted">{t("billing.no_overrides")}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <caption className="sr-only">{t("billing.overrides")}</caption>
            <thead className="bg-slate-50 text-left text-xs uppercase text-muted">
              <tr>
                <th scope="col" className="px-4 py-2">{t("billing.channel")}</th>
                <th scope="col" className="px-4 py-2 text-right">{t("billing.price")}</th>
                <th scope="col" className="px-4 py-2">{t("billing.dates")}</th>
                <th scope="col" className="px-4 py-2">{t("billing.col.status")}</th>
                <th scope="col" className="px-4 py-2"><span className="sr-only">{t("billing.actions")}</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {(overrides.data ?? []).map((row) => (
                <tr key={row.id} data-override={row.channel} data-state={row.state}>
                  <td className="px-4 py-2 text-ink">{t(`billing.channel.${row.channel}`)}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{formatRupees(row.price_paise)}</td>
                  <td className="px-4 py-2 text-muted">{formatDate(row.starts_on)} – {row.ends_on ? formatDate(row.ends_on) : t("billing.open_ended")}</td>
                  <td className="px-4 py-2"><StatusBadge status={row.state === "active" ? "active" : row.state === "scheduled" ? "draft" : "disabled"} label={t(`billing.state.${row.state}`)} /></td>
                  <td className="px-4 py-2 text-right">
                    {superAdmin && row.state === "scheduled" && (
                      <Button variant="ghost" onClick={() => run(() => unwrap(api.DELETE("/api/admin/tenants/{id}/price-overrides/{override_id}", overridePath(row.id))), t("billing.override_deleted"))}>
                        {t("billing.delete")}
                      </Button>
                    )}
                    {superAdmin && row.state === "active" && (
                      <Button variant="ghost" onClick={() => run(() => unwrap(api.PATCH("/api/admin/tenants/{id}/price-overrides/{override_id}", { ...overridePath(row.id), body: { ends_on: today } })), t("billing.override_ended"))}>
                        {t("billing.end_today")}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {superAdmin && (
        <div className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-[13rem_8rem_10rem_10rem_auto] sm:items-end">
          <SelectField label={t("billing.channel")} value={channel} options={CHANNELS.map((c) => ({ value: c, label: t(`billing.channel.${c}`) }))} onChange={(e) => setChannel(e.target.value)} />
          <TextField label={t("billing.price")} type="number" min={0} step="0.01" value={rupees} onChange={(e) => setRupees(e.target.value)} />
          <TextField label={t("billing.starts_on")} type="date" min={today} value={starts} onChange={(e) => setStarts(e.target.value)} />
          <TextField label={t("billing.ends_on")} type="date" min={starts} value={ends} hint={t("billing.ends_hint")} onChange={(e) => setEnds(e.target.value)} />
          <Button onClick={add} loading={busy}>{t("billing.add_override")}</Button>
        </div>
      )}
    </div>
  );
}

"use client";

import { Alert, cn, StatusPill, type StatusTone } from "@saas/ui";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { errorMessage, t } from "@/i18n";
import { formatDateTime } from "@/lib/format";
import { useDashboard } from "@/lib/queries";
import type { Dashboard as DashboardData } from "@/lib/types";

const QUALITY_TONES: Record<DashboardData["quality_rating"], StatusTone> = {
  GREEN: "ok",
  YELLOW: "warn",
  RED: "error",
  UNKNOWN: "neutral",
};

function Tile({
  label,
  value,
  hint,
  href,
  attention,
  muted,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  href?: string;
  attention?: boolean;
  muted?: boolean;
}) {
  const body = (
    <>
      <dt className="text-sm text-muted">{label}</dt>
      <dd className={cn("mt-2 text-3xl font-semibold tabular-nums", muted ? "text-slate-400" : "text-ink")}>
        {value}
      </dd>
      {hint && <dd className="mt-1 text-xs text-muted">{hint}</dd>}
    </>
  );
  const classes = cn(
    "block rounded-xl border bg-white p-4 shadow-sm",
    attention ? "border-amber-300 ring-1 ring-amber-200" : "border-slate-200",
    href && "transition hover:border-brand-300 hover:shadow",
  );
  return (
    <div data-testid="tile" data-label={label}>
      {href ? (
        <Link href={href} className={classes}>
          <dl>{body}</dl>
        </Link>
      ) : (
        <dl className={classes}>{body}</dl>
      )}
    </div>
  );
}

function Section({ title, badge, children }: { title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
        {badge}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{children}</div>
    </section>
  );
}

/** Dashboard tiles (spec 2.2). Message and webhook tiles read zero until the Phase 2 engine. */
export function Dashboard() {
  const dashboard = useDashboard();
  const data = dashboard.data;
  const dash = t("common.none");
  const value = (n: number | undefined) => (n === undefined ? dash : n.toLocaleString("en-IN"));
  const phase2 = data && !data.messages_live;

  return (
    <>
      <PageHeader
        title={t("page.dashboard.title")}
        description={
          data
            ? t("dashboard.updated", { time: formatDateTime(data.generated_at) })
            : t("page.dashboard.description")
        }
      />
      <div className="space-y-8">
        {dashboard.isError && <Alert>{errorMessage(dashboard.error)}</Alert>}
        {data?.quality_alert && <Alert>{t("dashboard.quality_red")}</Alert>}

        <Section title={t("dashboard.section.clients")}>
          <Tile label={t("dashboard.clients_active")} value={value(data?.clients_active)} href="/clients?status=active" />
          <Tile
            label={t("dashboard.clients_suspended")}
            value={value(data?.clients_suspended)}
            href="/clients?status=suspended"
            attention={Boolean(data?.clients_suspended)}
          />
        </Section>

        <Section
          title={t("dashboard.section.messages")}
          badge={phase2 && <StatusPill tone="neutral">{t("dashboard.phase2")}</StatusPill>}
        >
          {(["sent", "delivered", "failed", "replied"] as const).map((kind) => (
            <Tile
              key={kind}
              label={t(`dashboard.messages_${kind}`)}
              value={value(data?.messages_today[kind])}
              muted={Boolean(phase2)}
            />
          ))}
        </Section>

        <Section title={t("dashboard.section.health")}>
          <Tile
            label={t("dashboard.quality")}
            value={
              data ? (
                <StatusPill tone={QUALITY_TONES[data.quality_rating]}>
                  {t(`dashboard.quality.${data.quality_rating}`)}
                </StatusPill>
              ) : (
                dash
              )
            }
            hint={t("dashboard.quality_hint")}
            attention={data?.quality_alert}
          />
          <Tile
            label={t("dashboard.failed_webhooks")}
            value={value(data?.failed_webhooks_24h)}
            muted={Boolean(phase2)}
            hint={phase2 ? t("dashboard.phase2") : undefined}
          />
        </Section>

        <Section title={t("dashboard.section.attention")}>
          <Tile
            label={t("dashboard.templates_awaiting")}
            value={value(data?.templates_awaiting_action)}
            hint={t("dashboard.templates_awaiting_hint")}
            href="/whatsapp-templates"
            attention={Boolean(data?.templates_awaiting_action)}
          />
          <Tile label={t("dashboard.change_requests")} value={value(data?.open_change_requests)} />
          <Tile
            label={t("dashboard.support_sessions")}
            value={value(data?.active_support_sessions)}
            href="/audit-log?action=support_start"
            attention={Boolean(data?.active_support_sessions)}
          />
        </Section>
      </div>
    </>
  );
}

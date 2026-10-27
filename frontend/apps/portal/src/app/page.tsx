import { getHealth, type Health } from "@saas/api-client";

import { t } from "@/i18n";
import { comingNext } from "@/i18n/coming-next";

import { HealthCard } from "./health-card";

export const dynamic = "force-dynamic";

const apiUrl = process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function loadHealth(): Promise<Health | null> {
  try {
    return await getHealth(apiUrl);
  } catch {
    return null;
  }
}

export default async function Home() {
  const health = await loadHealth();

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
              SC
            </div>
            <div>
              <p className="text-sm font-semibold text-ink">{t("app.name")}</p>
              <p className="text-xs text-muted">{t("app.host")}</p>
            </div>
          </div>
          <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700">
            {t("scaffold.badge")}
          </span>
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl gap-6 px-6 py-10 md:grid-cols-5">
        <section className="md:col-span-3">
          <p className="text-sm font-medium text-brand-600">{t("app.tagline")}</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">{t("scaffold.title")}</h1>
          <p className="mt-3 text-sm leading-6 text-muted">{t("scaffold.body")}</p>

          <h2 className="mt-8 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("scaffold.coming")}
          </h2>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {comingNext.map((key) => (
              <li
                key={key}
                className="rounded-lg border border-dashed border-slate-300 bg-white px-3 py-2 text-sm text-slate-600"
              >
                {t(key)}
              </li>
            ))}
          </ul>
        </section>

        <div className="md:col-span-2">
          <HealthCard health={health} apiUrl={apiUrl} />
        </div>
      </main>

      <footer className="mx-auto max-w-5xl px-6 pb-10 text-xs text-muted">{t("footer.stack")}</footer>
    </div>
  );
}

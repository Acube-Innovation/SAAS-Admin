"use client";

import type { AuthState } from "@saas/api-client";
import { Button, EmptyState, Spinner } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { t } from "@/i18n";
import { api, ME_KEY, useMe } from "@/lib/api";
import { nextRoute } from "@/lib/auth";

import { icons } from "./icons";
import { Sidebar } from "./sidebar";
import { SupportBanner } from "./support-banner";

/**
 * Protected layout: checks the session and 2FA with the API on every load and sends the user to
 * sign-in or two-factor when needed. Only platform staff with 2FA done see the console.
 */
export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const me = useMe();
  const [menuOpen, setMenuOpen] = useState(false);
  const route = me.isPending ? undefined : nextRoute(me.data);

  useEffect(() => {
    if (route === "/login") router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    if (route === "/two-factor") router.replace(`/two-factor?next=${encodeURIComponent(pathname)}`);
  }, [route, router, pathname]);

  async function signOut() {
    await api.POST("/api/auth/logout");
    queryClient.setQueryData(ME_KEY, null);
    queryClient.clear();
    router.replace("/login");
  }

  if (route === undefined || route === "/login" || route === "/two-factor") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label={t("app.loading")} />
      </div>
    );
  }

  if (route === "denied") {
    return (
      <div className="mx-auto max-w-lg px-4 py-24">
        <EmptyState
          title={t("denied.title")}
          description={t("denied.body")}
          action={
            <Button variant="secondary" onClick={signOut}>
              {t("account.sign_out")}
            </Button>
          }
        />
      </div>
    );
  }

  const user = me.data as AuthState;
  return (
    <div className="min-h-screen bg-slate-50">
      <aside className="fixed inset-y-0 left-0 hidden w-64 lg:block">
        <Sidebar />
      </aside>
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div aria-hidden className="absolute inset-0 bg-slate-900/50" onClick={() => setMenuOpen(false)} />
          <aside className="relative h-full w-64">
            <Sidebar onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      )}

      <div className="lg:pl-64">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-slate-200 bg-white px-4 sm:px-6">
          <button
            type="button"
            className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 lg:hidden"
            aria-label={menuOpen ? t("nav.close_menu") : t("nav.open_menu")}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? icons.close : icons.menu}
          </button>
          <div className="ml-auto flex items-center gap-4">
            <div className="text-right">
              <p className="text-sm font-medium text-ink">{user.full_name || user.email}</p>
              <p className="text-xs text-muted">
                {t(`role.${user.platform_role}`)} · {user.email}
              </p>
            </div>
            <Button variant="ghost" onClick={signOut} aria-label={t("account.sign_out")}>
              {icons.signOut}
              <span className="hidden sm:inline">{t("account.sign_out")}</span>
            </Button>
          </div>
        </header>
        <SupportBanner />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>
      </div>
    </div>
  );
}

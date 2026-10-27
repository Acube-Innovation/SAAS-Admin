"use client";

import { cn } from "@saas/ui";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { t } from "@/i18n";

import { Brand } from "./brand";
import { icons } from "./icons";

export const NAV_ITEMS = [
  { href: "/dashboard", label: "nav.dashboard", icon: icons.dashboard },
  { href: "/clients", label: "nav.clients", icon: icons.clients },
  { href: "/industry-templates", label: "nav.industry_templates", icon: icons.industry },
  { href: "/whatsapp-templates", label: "nav.whatsapp_templates", icon: icons.whatsapp },
  { href: "/audit-log", label: "nav.audit_log", icon: icons.audit },
] as const;

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <div className="flex h-full flex-col bg-slate-900 px-4 py-5">
      <div className="px-2">
        <Brand inverted />
      </div>
      <nav aria-label={t("nav.main")} className="mt-8 flex-1">
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium",
                    active ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800/60 hover:text-white",
                  )}
                >
                  {item.icon}
                  {t(item.label)}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

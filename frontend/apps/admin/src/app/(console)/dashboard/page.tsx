import type { Metadata } from "next";

import { Dashboard } from "@/components/dashboard/dashboard";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.dashboard.title") };

export default function DashboardPage() {
  return <Dashboard />;
}

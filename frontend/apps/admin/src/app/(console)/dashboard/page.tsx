import type { Metadata } from "next";

import { icons } from "@/components/icons";
import { PlaceholderPage } from "@/components/placeholder-page";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.dashboard.title") };

export default function DashboardPage() {
  return <PlaceholderPage page="dashboard" icon={icons.dashboard} />;
}

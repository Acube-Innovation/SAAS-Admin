import type { Metadata } from "next";

import { icons } from "@/components/icons";
import { PlaceholderPage } from "@/components/placeholder-page";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.clients.title") };

export default function ClientsPage() {
  return <PlaceholderPage page="clients" icon={icons.clients} />;
}

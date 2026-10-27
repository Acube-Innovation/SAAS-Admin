import type { Metadata } from "next";

import { icons } from "@/components/icons";
import { PlaceholderPage } from "@/components/placeholder-page";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.whatsapp_templates.title") };

export default function WhatsappTemplatesPage() {
  return <PlaceholderPage page="whatsapp_templates" icon={icons.whatsapp} />;
}

import type { Metadata } from "next";

import { icons } from "@/components/icons";
import { PlaceholderPage } from "@/components/placeholder-page";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.industry_templates.title") };

export default function IndustryTemplatesPage() {
  return <PlaceholderPage page="industry_templates" icon={icons.industry} />;
}

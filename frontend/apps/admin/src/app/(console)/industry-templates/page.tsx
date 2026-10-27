import type { Metadata } from "next";

import { IndustryList } from "@/components/industry/industry-list";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.industry_templates.title") };

export default function IndustryTemplatesPage() {
  return <IndustryList />;
}

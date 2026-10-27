import type { Metadata } from "next";

import { icons } from "@/components/icons";
import { PlaceholderPage } from "@/components/placeholder-page";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.audit_log.title") };

export default function AuditLogPage() {
  return <PlaceholderPage page="audit_log" icon={icons.audit} />;
}

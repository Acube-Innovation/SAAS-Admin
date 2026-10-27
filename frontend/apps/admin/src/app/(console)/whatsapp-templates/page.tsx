import type { Metadata } from "next";
import { Suspense } from "react";

import { WhatsAppList } from "@/components/whatsapp/wa-list";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.whatsapp_templates.title") };

export default function Page() {
  return (
    <Suspense>
      <WhatsAppList />
    </Suspense>
  );
}

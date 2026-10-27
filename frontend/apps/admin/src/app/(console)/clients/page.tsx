import type { Metadata } from "next";
import { Suspense } from "react";

import { ClientsList } from "@/components/client/clients-list";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.clients.title") };

export default function ClientsPage() {
  return (
    <Suspense>
      <ClientsList />
    </Suspense>
  );
}

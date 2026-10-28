import type { Metadata } from "next";
import { Suspense } from "react";

import { ChangeRequestQueue } from "@/components/change-requests/queue";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.change_requests.title") };

export default function ChangeRequestsPage() {
  return (
    <Suspense>
      <ChangeRequestQueue />
    </Suspense>
  );
}

import type { Metadata } from "next";
import { Suspense } from "react";

import { PlansAndPrices } from "@/components/billing/plans-prices";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.billing.title") };

export default function BillingPage() {
  return (
    <Suspense>
      <PlansAndPrices />
    </Suspense>
  );
}

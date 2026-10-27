import type { Metadata } from "next";
import { Suspense } from "react";

import { AuditLog } from "@/components/audit/audit-log";
import { t } from "@/i18n";

export const metadata: Metadata = { title: t("page.audit_log.title") };

export default function AuditLogPage() {
  return (
    <Suspense>
      <AuditLog />
    </Suspense>
  );
}

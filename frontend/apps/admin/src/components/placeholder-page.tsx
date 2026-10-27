import { EmptyState } from "@saas/ui";

import { t } from "@/i18n";

import { PageHeader } from "./page-header";

/** An empty console page until its screen is built in a later step. */
export function PlaceholderPage({ page, icon }: { page: string; icon: React.ReactNode }) {
  return (
    <>
      <PageHeader title={t(`page.${page}.title`)} description={t(`page.${page}.description`)} />
      <EmptyState icon={icon} title={t("page.coming_soon")} description={t(`page.${page}.empty`)} />
    </>
  );
}

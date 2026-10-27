import { Button } from "@saas/ui";

import { t } from "@/i18n";

const PAGE_SIZE = 25;

export function Pagination({
  page,
  count,
  onChange,
}: {
  page: number;
  count: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-muted">
      <span>{t("common.results", { count })}</span>
      <div className="flex items-center gap-3">
        <span>{t("common.page", { page, pages })}</span>
        <Button variant="secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          {t("common.previous")}
        </Button>
        <Button variant="secondary" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          {t("common.next")}
        </Button>
      </div>
    </div>
  );
}

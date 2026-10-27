import { t } from "@/i18n";

export function Brand({ inverted = false }: { inverted?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
        SC
      </div>
      <div>
        <p className={inverted ? "text-sm font-semibold text-white" : "text-sm font-semibold text-ink"}>
          {t("app.product")}
        </p>
        <p className={inverted ? "text-xs text-slate-400" : "text-xs text-muted"}>{t("app.name")}</p>
      </div>
    </div>
  );
}

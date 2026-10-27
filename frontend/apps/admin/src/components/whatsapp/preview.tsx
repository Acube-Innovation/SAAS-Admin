import { cn } from "@saas/ui";

import { t } from "@/i18n";

import { type Content, renderWithSamples } from "./content";

function Parts({ text, samples }: { text: string; samples: Record<string, string> }) {
  return (
    <>
      {renderWithSamples(text, samples).map((part, index) =>
        part.variable ? (
          <span
            key={index}
            title={`{{${part.variable}}}`}
            className={cn("rounded px-0.5", part.missing ? "bg-amber-200 text-amber-900" : "bg-emerald-100/70")}
          >
            {part.text}
          </span>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

/** How the message looks in WhatsApp, with sample values filled in (spec 2.7). */
export function WhatsAppPreview({ content, agencyName }: { content: Content; agencyName: string }) {
  const samples = { agency_name: agencyName, ...content.sample_values };
  return (
    <figure aria-label={t("wa.preview")} className="rounded-2xl bg-[#efe7dd] p-4">
      <div className="max-w-sm rounded-xl rounded-tl-none bg-white text-[0.9rem] leading-snug text-slate-900 shadow">
        <div className="space-y-1.5 px-3 pt-2.5 pb-2">
          {content.header_text && (
            <p className="font-semibold">
              <Parts text={content.header_text} samples={samples} />
            </p>
          )}
          <p className="whitespace-pre-wrap">
            <Parts text={content.body || "…"} samples={samples} />
          </p>
          {content.footer && <p className="text-xs text-slate-500">{content.footer}</p>}
          <p className="text-right text-[0.65rem] text-slate-400">10:00</p>
        </div>
        {content.buttons.length > 0 && (
          <div className="divide-y divide-slate-100 border-t border-slate-100">
            {content.buttons.map((button, index) => (
              <div key={index} className="flex items-center justify-center gap-1.5 py-2 text-sm font-medium text-sky-600">
                <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M9 14 4 9l5-5" />
                  <path d="M4 9h10a6 6 0 0 1 0 12h-3" />
                </svg>
                {button.text || "…"}
              </div>
            ))}
          </div>
        )}
      </div>
    </figure>
  );
}

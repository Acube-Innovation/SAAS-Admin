"use client";

import { cn } from "@saas/ui";

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-slate-200">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`tab-${tab.id}`}
          aria-selected={tab.id === active}
          aria-controls={`panel-${tab.id}`}
          onClick={() => onChange(tab.id)}
          className={cn(
            "-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium",
            tab.id === active
              ? "border-brand-600 text-brand-700"
              : "border-transparent text-muted hover:border-slate-300 hover:text-ink",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

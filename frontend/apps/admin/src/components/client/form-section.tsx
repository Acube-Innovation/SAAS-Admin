export function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <legend className="sr-only">{title}</legend>
      <h2 className="mb-4 text-sm font-semibold text-ink">{title}</h2>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

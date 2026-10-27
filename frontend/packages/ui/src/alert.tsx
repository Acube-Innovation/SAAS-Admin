import { cn } from "./cn";

const tones = {
  error: "bg-red-50 text-red-800 ring-red-600/20",
  warn: "bg-amber-50 text-amber-900 ring-amber-600/20",
  info: "bg-sky-50 text-sky-900 ring-sky-600/20",
  success: "bg-emerald-50 text-emerald-900 ring-emerald-600/20",
};

export function Alert({
  tone = "error",
  title,
  children,
}: {
  tone?: keyof typeof tones;
  title?: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("rounded-lg px-4 py-3 text-sm ring-1 ring-inset", tones[tone])}
    >
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={title ? "mt-1" : undefined}>{children}</div>}
    </div>
  );
}

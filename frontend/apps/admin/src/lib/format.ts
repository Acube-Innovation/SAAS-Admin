const TIME_ZONE = "Asia/Kolkata";

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: TIME_ZONE,
  }).format(new Date(value));
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: TIME_ZONE }).format(
    new Date(value),
  );
}

/** Seconds as m:ss (support session countdown). */
export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

/** Paise as rupees, e.g. 199900 -> "₹1,999.00". */
export function formatRupees(paise: number): string {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2 }).format(paise / 100);
}

/** Today in India as YYYY-MM-DD. */
export function istToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: TIME_ZONE });
}

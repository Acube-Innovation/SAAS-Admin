/** Audit log screen helpers: URL filters, the before/after comparison and the export filename. */

export const AUDIT_FILTERS = ["tenant", "actor", "action", "object_type", "date_from", "date_to"] as const;
export type AuditFilter = (typeof AUDIT_FILTERS)[number];
export type AuditFilters = Record<AuditFilter, string>;

export const AUDIT_PAGE_SIZE = 50;

export function readAuditFilters(params: URLSearchParams): AuditFilters {
  return Object.fromEntries(AUDIT_FILTERS.map((key) => [key, params.get(key) ?? ""])) as AuditFilters;
}

/** Only the filters that are set, ready for a query string. */
export function activeFilters(filters: Partial<AuditFilters>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) => typeof value === "string" && value.trim() !== ""),
  ) as Record<string, string>;
}

export type DiffRow = { field: string; before: string; after: string; changed: boolean };

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function show(value: unknown): string {
  if (value === undefined) return "";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/**
 * One row per field in before or after, changed fields first (each group in after's order). A
 * missing field counts as empty, so "" on one side and nothing on the other is not a change.
 */
export function diffRows(before: unknown, after: unknown): DiffRow[] {
  const old = asRecord(before);
  const next = asRecord(after);
  const fields = [...new Set([...Object.keys(next), ...Object.keys(old)])];
  const rows = fields.map((field) => {
    const a = show(old[field]);
    const b = show(next[field]);
    return { field, before: a, after: b, changed: a !== b };
  });
  return [...rows.filter((row) => row.changed), ...rows.filter((row) => !row.changed)];
}

/** The filename from a Content-Disposition header, or a fallback. */
export function filenameFrom(header: string | null, fallback = "audit-log.csv"): string {
  const match = header?.match(/filename="?([^";]+)"?/i);
  return match?.[1] ?? fallback;
}

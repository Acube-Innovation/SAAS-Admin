import { describe, expect, it } from "vitest";

import { activeFilters, diffRows, filenameFrom, readAuditFilters } from "./audit";

describe("audit helpers", () => {
  it("reads every filter from the URL, defaulting to empty", () => {
    const filters = readAuditFilters(new URLSearchParams("action=create&date_from=2026-10-20&page=2"));
    expect(filters).toEqual({
      tenant: "",
      actor: "",
      action: "create",
      object_type: "",
      date_from: "2026-10-20",
      date_to: "",
    });
    expect(activeFilters(filters)).toEqual({ action: "create", date_from: "2026-10-20" });
  });

  it("compares before and after field by field", () => {
    const rows = diffRows(
      { city: "Kochi", status: "active", gstin: "32ABCDE1234F1Z5" },
      { city: "Ernakulam", status: "active", contact: { name: "Anil" } },
    );
    expect(rows).toEqual([
      { field: "city", before: "Kochi", after: "Ernakulam", changed: true },
      { field: "contact", before: "", after: '{"name":"Anil"}', changed: true },
      { field: "gstin", before: "32ABCDE1234F1Z5", after: "", changed: true },
      { field: "status", before: "active", after: "active", changed: false },
    ]);
  });

  it("treats a missing field as empty", () => {
    expect(diffRows({ city: "Kochi" }, { city: "Kochi", note: "" })).toEqual([
      { field: "city", before: "Kochi", after: "Kochi", changed: false },
      { field: "note", before: "", after: "", changed: false },
    ]);
  });

  it("handles a create (no before) and a delete (no after)", () => {
    expect(diffRows(null, { name: "Kochi Gas" })).toEqual([
      { field: "name", before: "", after: "Kochi Gas", changed: true },
    ]);
    expect(diffRows({ ok: false }, undefined)).toEqual([{ field: "ok", before: "false", after: "", changed: true }]);
    expect(diffRows({ x: null }, { x: null })[0]).toMatchObject({ before: "null", changed: false });
  });

  it("takes the filename from Content-Disposition", () => {
    expect(filenameFrom('attachment; filename="audit-log-20261027-1015.csv"')).toBe("audit-log-20261027-1015.csv");
    expect(filenameFrom(null)).toBe("audit-log.csv");
  });
});

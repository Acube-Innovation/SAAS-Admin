import type { AuthState } from "@saas/api-client";

/** Spec 1.1: only super admins create, edit, suspend and activate clients. */
export function isSuperAdmin(me: AuthState | null | undefined): boolean {
  return me?.platform_role === "super_admin";
}

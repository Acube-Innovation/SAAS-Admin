import type { AuthState } from "@saas/api-client";

export const PUBLIC_PATHS = ["/login", "/two-factor", "/invite"];
export const HOME = "/dashboard";

export type Route = "/login" | "/two-factor" | "denied" | null;

/**
 * Where a user must go before using the console: sign in, finish 2FA, or nowhere (allowed).
 * Platform staff always need 2FA (spec 1); anyone else is refused.
 */
export function nextRoute(state: AuthState | null | undefined): Route {
  if (!state) return "/login";
  if (state.otp_setup_required || state.otp_required) return "/two-factor";
  if (!state.platform_role) return "denied";
  return null;
}

/** Only same-site paths are followed after sign-in (no open redirects). */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return HOME;
  if (PUBLIC_PATHS.some((path) => next.startsWith(path))) return HOME;
  return next;
}

/** Base32 secret from an otpauth:// URL, grouped in fours for manual entry. */
export function setupKey(otpauthUrl: string): string {
  const secret = new URL(otpauthUrl).searchParams.get("secret") ?? "";
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

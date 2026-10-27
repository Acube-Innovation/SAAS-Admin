"use client";

import { ApiError, type AuthState, createApiClient, readCookie, unwrap } from "@saas/api-client";
import { useQuery } from "@tanstack/react-query";

/**
 * Same-origin client: Next.js rewrites /api/* to the Django API (see next.config.ts). fetch is
 * looked up on each call so tests can replace it.
 */
export const api = createApiClient({
  baseUrl: typeof window === "undefined" ? "" : window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export const ME_KEY = ["auth", "me"] as const;

/**
 * Make sure the csrftoken cookie exists before the first unsafe request. Called when a form
 * loads and again on submit, so a fast first submit can't race the cookie.
 */
export async function ensureCsrf(): Promise<void> {
  if (!readCookie("csrftoken")) await api.GET("/api/auth/csrf");
}

/** The signed-in user, or null when there is no session. */
export async function fetchMe(): Promise<AuthState | null> {
  try {
    return await unwrap(api.GET("/api/auth/me"));
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return null;
    throw error;
  }
}

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe, retry: false, staleTime: 30_000 });
}

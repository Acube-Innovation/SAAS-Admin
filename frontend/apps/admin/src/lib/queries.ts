"use client";

import { ApiError, unwrap } from "@saas/api-client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import type { SupportSession } from "./types";

export const keys = {
  tenants: (params: Record<string, string>) => ["tenants", params] as const,
  tenant: (id: string) => ["tenant", id] as const,
  tenantUsers: (id: string) => ["tenant", id, "users"] as const,
  tenantActivity: (id: string, page: number) => ["tenant", id, "activity", page] as const,
  tenantTemplates: (id: string) => ["tenant", id, "templates"] as const,
  referenceData: ["reference-data"] as const,
  industryTemplates: ["industry-templates"] as const,
  supportSession: ["support-session"] as const,
};

export function useTenants(params: { search?: string; status?: string; industry_template?: string; page?: string }) {
  const query = Object.fromEntries(Object.entries(params).filter(([, value]) => value)) as Record<string, string>;
  return useQuery({
    queryKey: keys.tenants(query),
    queryFn: () => unwrap(api.GET("/api/admin/tenants", { params: { query } })),
    placeholderData: keepPreviousData,
  });
}

export function useTenant(id: string) {
  return useQuery({
    queryKey: keys.tenant(id),
    queryFn: () => unwrap(api.GET("/api/admin/tenants/{id}", { params: { path: { id } } })),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
}

export function useTenantUsers(id: string) {
  return useQuery({
    queryKey: keys.tenantUsers(id),
    queryFn: () => unwrap(api.GET("/api/admin/tenants/{id}/users", { params: { path: { id } } })),
  });
}

export function useTenantActivity(id: string, page: number) {
  return useQuery({
    queryKey: keys.tenantActivity(id, page),
    queryFn: () =>
      unwrap(
        api.GET("/api/admin/tenants/{id}/activity", { params: { path: { id }, query: { page } } }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useTenantTemplates(id: string) {
  return useQuery({
    queryKey: keys.tenantTemplates(id),
    queryFn: () =>
      unwrap(api.GET("/api/admin/whatsapp-templates", { params: { query: { tenant: id } } })),
  });
}

export function useReferenceData() {
  return useQuery({
    queryKey: keys.referenceData,
    queryFn: () => unwrap(api.GET("/api/admin/reference-data")),
    staleTime: Infinity,
  });
}

export function useIndustryTemplates() {
  return useQuery({
    queryKey: keys.industryTemplates,
    queryFn: () => unwrap(api.GET("/api/admin/industry-templates")),
    staleTime: 60_000,
  });
}

/** The signed-in staff user's active support session, or null (the API answers 204). */
export function useSupportSession() {
  return useQuery({
    queryKey: keys.supportSession,
    queryFn: async (): Promise<SupportSession | null> => {
      const result = await api.GET("/api/admin/support-sessions/current");
      if (result.response.status === 204) return null;
      return unwrap(Promise.resolve(result)) as Promise<SupportSession>;
    },
    refetchInterval: 30_000,
  });
}

export function useEndSupportSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/admin/support-sessions/{id}/end", { params: { path: { id } } })),
    onSuccess: () => queryClient.setQueryData(keys.supportSession, null),
  });
}

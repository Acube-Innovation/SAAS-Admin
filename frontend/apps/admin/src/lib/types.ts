import type { components } from "@saas/api-client";

type Schemas = components["schemas"];
export type Tenant = Schemas["Tenant"];
export type TenantCreate = Schemas["TenantCreateRequest"];
export type MembershipAdmin = Schemas["MembershipAdmin"];
export type SupportSession = Schemas["SupportSession"];
export type AuditLogEntry = Schemas["AuditLog"];
export type IndustryTemplate = Schemas["IndustryTemplate"];
export type ReferenceData = Schemas["ReferenceData"];
export type WhatsAppTemplate = Schemas["Template"];
export type State = { code: string; name: string; gst_code: string };

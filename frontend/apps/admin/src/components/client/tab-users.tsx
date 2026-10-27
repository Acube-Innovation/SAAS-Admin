"use client";

import { unwrap } from "@saas/api-client";
import { Alert, Button, type Column, DataTable, StatusBadge } from "@saas/ui";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage, t } from "@/i18n";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useTenantUsers } from "@/lib/queries";
import type { MembershipAdmin } from "@/lib/types";

/** Users tab: memberships with 2FA state; resend invite for invited users (Workflow 3.1). */
export function UsersTab({ tenantId }: { tenantId: string }) {
  const users = useTenantUsers(tenantId);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string }>();
  const resend = useMutation({
    mutationFn: (row: MembershipAdmin) =>
      unwrap(
        api.POST("/api/admin/tenants/{id}/users/{membership_id}/resend-invite", {
          params: { path: { id: tenantId, membership_id: row.id } },
        }),
      ),
    onSuccess: (_data, row) => setNotice({ tone: "success", text: t("users.resent", { email: row.email }) }),
    onError: (error) => setNotice({ tone: "error", text: errorMessage(error) }),
  });

  const columns: Column<MembershipAdmin>[] = [
    { key: "name", header: t("users.col.name"), render: (row) => row.full_name || t("common.none") },
    { key: "email", header: t("users.col.email"), render: (row) => row.email },
    { key: "role", header: t("users.col.role"), render: (row) => t(`role.${row.role}`) },
    { key: "status", header: t("users.col.status"), render: (row) => <StatusBadge status={row.status} label={t(`status.${row.status}`)} /> },
    { key: "login", header: t("users.col.last_login"), render: (row) => (row.last_login ? formatDateTime(row.last_login) : t("users.never")) },
    { key: "otp", header: t("users.col.otp"), render: (row) => (row.otp_enabled ? t("common.yes") : t("common.no")) },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (row) =>
        row.status === "invited" && (
          <Button
            variant="secondary"
            loading={resend.isPending && resend.variables?.id === row.id}
            onClick={() => resend.mutate(row)}
          >
            {t("users.resend")}
          </Button>
        ),
    },
  ];

  return (
    <div className="space-y-3">
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      {users.isError && <Alert>{errorMessage(users.error)}</Alert>}
      <DataTable
        caption={t("client.tab.users")}
        columns={columns}
        rows={users.data}
        rowKey={(row) => row.id}
        loading={users.isPending}
        loadingLabel={t("app.loading")}
        empty={t("users.empty")}
      />
    </div>
  );
}

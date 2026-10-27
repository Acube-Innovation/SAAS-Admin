"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { unwrap } from "@saas/api-client";
import { newPasswordSchema } from "@saas/api-client/schemas";
import { Alert, Button, Spinner, TextField } from "@saas/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { errorMessage, fieldError, t } from "@/i18n";
import { api, ensureCsrf, ME_KEY } from "@/lib/api";
import { HOME, nextRoute } from "@/lib/auth";

const existingUserSchema = z.object({ password: z.string().min(1, "validation.required") });

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string>();
  const invite = useQuery({
    queryKey: ["invite", token],
    queryFn: () => unwrap(api.GET("/api/auth/invites/{token}", { params: { path: { token } } })),
    retry: false,
  });
  const newUser = invite.data?.needs_new_password ?? true;
  const form = useForm<{ full_name?: string; password: string; password_confirm?: string }>({
    resolver: zodResolver(newUser ? newPasswordSchema : existingUserSchema),
    defaultValues: { full_name: "", password: "", password_confirm: "" },
  });

  useEffect(() => {
    void ensureCsrf();
  }, []);

  async function onSubmit(values: { full_name?: string; password: string }) {
    setError(undefined);
    try {
      await ensureCsrf();
      const state = await unwrap(
        api.POST("/api/auth/invites/{token}/accept", {
          params: { path: { token } },
          body: { password: values.password, full_name: values.full_name ?? "" },
        }),
      );
      queryClient.setQueryData(ME_KEY, state);
      router.replace(nextRoute(state) === "/two-factor" ? "/two-factor" : HOME);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  if (invite.isPending) return <Spinner label={t("app.loading")} />;
  if (invite.isError) {
    return (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold text-ink">{t("invite.unavailable_title")}</h1>
        <Alert>{errorMessage(invite.error)}</Alert>
        <Link href="/login" className="text-sm font-medium text-brand-600 hover:text-brand-700">
          {t("invite.back_to_login")}
        </Link>
      </div>
    );
  }

  const data = invite.data;
  const errors = form.formState.errors;
  return (
    <>
      <h1 className="text-lg font-semibold text-ink">{t("invite.title")}</h1>
      <p className="mt-1 text-sm text-muted">
        {t("invite.intro", { tenant: data.tenant_name, role: t(`role.${data.role}`) })}
      </p>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="mt-6 space-y-4">
        {error && <Alert>{error}</Alert>}
        <TextField label={t("invite.email")} value={data.email} readOnly disabled />
        {newUser ? (
          <>
            <TextField
              label={t("invite.full_name")}
              autoComplete="name"
              error={fieldError(errors.full_name)}
              {...form.register("full_name")}
            />
            <TextField
              label={t("invite.password")}
              type="password"
              autoComplete="new-password"
              required
              hint={t("invite.password_hint")}
              error={fieldError(errors.password)}
              {...form.register("password")}
            />
            <TextField
              label={t("invite.password_confirm")}
              type="password"
              autoComplete="new-password"
              required
              error={fieldError(errors.password_confirm)}
              {...form.register("password_confirm")}
            />
          </>
        ) : (
          <TextField
            label={t("invite.current_password")}
            type="password"
            autoComplete="current-password"
            required
            hint={t("invite.current_password_hint")}
            error={fieldError(errors.password)}
            {...form.register("password")}
          />
        )}
        <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>
          {t("invite.submit")}
        </Button>
        <p className="text-center text-xs text-muted">
          {t("invite.expires", { date: new Date(data.expires_at).toLocaleString("en-IN") })}
        </p>
      </form>
    </>
  );
}

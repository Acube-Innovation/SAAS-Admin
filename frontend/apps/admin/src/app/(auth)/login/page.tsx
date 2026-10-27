"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { unwrap } from "@saas/api-client";
import { loginSchema } from "@saas/api-client/schemas";
import { Alert, Button, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { errorMessage, fieldError, t } from "@/i18n";
import { api, ensureCsrf, fetchMe, ME_KEY } from "@/lib/api";
import { nextRoute, safeNext } from "@/lib/auth";

type Values = z.infer<typeof loginSchema>;

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string>();
  const form = useForm<Values>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } });
  const next = safeNext(params.get("next"));

  useEffect(() => {
    void ensureCsrf();
    // Already fully signed in? Go straight to the console.
    void fetchMe().then((state) => {
      if (state && nextRoute(state) === null) router.replace(next);
    });
  }, [router, next]);

  async function onSubmit(values: Values) {
    setError(undefined);
    try {
      await ensureCsrf();
      const state = await unwrap(api.POST("/api/auth/login", { body: values }));
      queryClient.setQueryData(ME_KEY, state);
      const route = nextRoute(state);
      if (route === "denied") {
        setError(t("login.not_staff"));
        await api.POST("/api/auth/logout");
        queryClient.setQueryData(ME_KEY, null);
        return;
      }
      router.replace(route === "/two-factor" ? `/two-factor?next=${encodeURIComponent(next)}` : next);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  const errors = form.formState.errors;
  return (
    <>
      <h1 className="text-lg font-semibold text-ink">{t("login.title")}</h1>
      <p className="mt-1 text-sm text-muted">{t("login.subtitle")}</p>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="mt-6 space-y-4">
        {error && <Alert>{error}</Alert>}
        <TextField
          label={t("login.email")}
          type="email"
          autoComplete="username"
          required
          error={fieldError(errors.email)}
          {...form.register("email")}
        />
        <TextField
          label={t("login.password")}
          type="password"
          autoComplete="current-password"
          required
          error={fieldError(errors.password)}
          {...form.register("password")}
        />
        <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>
          {t("login.submit")}
        </Button>
      </form>
    </>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

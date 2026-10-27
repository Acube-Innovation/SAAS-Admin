"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { unwrap } from "@saas/api-client";
import { otpSchema } from "@saas/api-client/schemas";
import { Alert, Button, Spinner, TextField } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import { Suspense, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { errorMessage, fieldError, t } from "@/i18n";
import { api, ME_KEY, useMe } from "@/lib/api";
import { nextRoute, safeNext, setupKey } from "@/lib/auth";

type Values = z.infer<typeof otpSchema>;

function TwoFactor() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const me = useMe();
  const [setup, setSetup] = useState<{ otpauth_url: string } | null>(null);
  const [error, setError] = useState<string>();
  const startedSetup = useRef(false);
  const form = useForm<Values>({ resolver: zodResolver(otpSchema), defaultValues: { otp_code: "" } });
  const next = safeNext(params.get("next"));
  const state = me.data;
  const settingUp = Boolean(state?.otp_setup_required);

  useEffect(() => {
    if (me.isPending) return;
    if (!state) return router.replace("/login");
    const route = nextRoute(state);
    if (route === null || route === "denied") router.replace(next);
  }, [me.isPending, state, router, next]);

  useEffect(() => {
    if (!settingUp || startedSetup.current) return;
    startedSetup.current = true;
    unwrap(api.POST("/api/auth/otp/setup"))
      .then((data) => setSetup(data))
      .catch((caught) => setError(errorMessage(caught)));
  }, [settingUp]);

  async function onSubmit(values: Values) {
    setError(undefined);
    try {
      const path = settingUp ? "/api/auth/otp/confirm" : "/api/auth/otp/verify";
      const updated = await unwrap(api.POST(path, { body: values }));
      queryClient.setQueryData(ME_KEY, updated);
      router.replace(next);
    } catch (caught) {
      form.reset({ otp_code: "" });
      setError(errorMessage(caught));
    }
  }

  async function signOut() {
    await api.POST("/api/auth/logout");
    queryClient.setQueryData(ME_KEY, null);
    router.replace("/login");
  }

  if (me.isPending || !state) return <Spinner label={t("app.loading")} />;

  return (
    <>
      <h1 className="text-lg font-semibold text-ink">
        {settingUp ? t("otp.setup_title") : t("otp.verify_title")}
      </h1>
      <p className="mt-1 text-sm text-muted">{settingUp ? t("otp.setup_intro") : t("otp.verify_intro")}</p>

      {settingUp && (
        <div className="mt-5 flex flex-col items-center gap-3 rounded-xl bg-slate-50 p-4">
          {setup ? (
            <>
              <QRCodeSVG value={setup.otpauth_url} size={168} title={t("otp.qr_label")} />
              <p className="text-center text-xs text-muted">{t("otp.setup_key")}</p>
              <code className="select-all rounded bg-white px-2 py-1 text-center font-mono text-sm tracking-wider text-ink ring-1 ring-slate-200">
                {setupKey(setup.otpauth_url)}
              </code>
            </>
          ) : (
            <Spinner />
          )}
        </div>
      )}

      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className="mt-5 space-y-4">
        {error && <Alert>{error}</Alert>}
        <TextField
          label={t("otp.code")}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          className="text-center font-mono text-lg tracking-[0.4em]"
          error={fieldError(form.formState.errors.otp_code)}
          {...form.register("otp_code")}
        />
        <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>
          {settingUp ? t("otp.confirm") : t("otp.verify")}
        </Button>
      </form>
      <button
        type="button"
        onClick={signOut}
        className="mt-4 w-full text-center text-sm font-medium text-brand-600 hover:text-brand-700"
      >
        {t("otp.other_account")}
      </button>
    </>
  );
}

export default function TwoFactorPage() {
  return (
    <Suspense>
      <TwoFactor />
    </Suspense>
  );
}

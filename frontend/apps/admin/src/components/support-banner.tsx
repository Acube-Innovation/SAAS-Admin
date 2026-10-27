"use client";

import { Button } from "@saas/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { errorMessage, t } from "@/i18n";
import { formatCountdown } from "@/lib/format";
import { keys, useEndSupportSession, useSupportSession } from "@/lib/queries";

/** Shown on every console page while the user has an active support session (Workflow 3.3). */
export function SupportBanner() {
  const session = useSupportSession();
  const end = useEndSupportSession();
  const queryClient = useQueryClient();
  const [now, setNow] = useState(() => Date.now());
  const data = session.data;

  useEffect(() => {
    if (!data) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [data]);

  const secondsLeft = data ? (new Date(data.expires_at).getTime() - now) / 1000 : 0;
  useEffect(() => {
    if (data && secondsLeft <= 0) void queryClient.invalidateQueries({ queryKey: keys.supportSession });
  }, [data, secondsLeft, queryClient]);

  if (!data || secondsLeft <= 0) return null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 bg-amber-100 px-4 py-2.5 text-sm text-amber-950 sm:px-6"
    >
      <p>
        <span className="font-semibold">{t("support.banner", { tenant: data.tenant_name })}</span>{" "}
        <span className="tabular-nums">{t("support.time_left", { time: formatCountdown(secondsLeft) })}</span>
      </p>
      <div className="flex items-center gap-3">
        {end.isError && <span className="text-red-700">{errorMessage(end.error)}</span>}
        <Button variant="secondary" loading={end.isPending} onClick={() => end.mutate(data.id)}>
          {t("support.end")}
        </Button>
      </div>
    </div>
  );
}

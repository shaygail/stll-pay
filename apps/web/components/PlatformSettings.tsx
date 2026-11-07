"use client";

import { useEffect, useState } from "react";
import { Button, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

type Settings = { multi_merchant_enabled: boolean; updated_at: string };

/**
 * The feature flag that opens STLL PAY to other businesses. Off at launch: STLL HAUS only.
 * Turning it on reopens sign-up and onboarding; nothing else needs rebuilding.
 */
export function PlatformSettings({ onStepUp }: { onStepUp: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<Settings>("/v1/admin/platform-settings").then(
      (s) => alive && setSettings(s),
      (err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.code === "step_up_required") onStepUp();
        else setError(err instanceof ApiError ? err.message : "Couldn't load settings.");
      },
    );
    return () => {
      alive = false;
    };
  }, [onStepUp]);

  async function toggle() {
    if (!settings) return;
    const next = !settings.multi_merchant_enabled;
    const reason = window.prompt(
      next
        ? "Open STLL PAY to other businesses (public sign-up and onboarding). Reason:"
        : "Close STLL PAY to new businesses. Reason:",
    );
    if (!reason || reason.trim().length < 3) return;
    try {
      setSettings(await api<Settings>("/v1/admin/platform-settings", { method: "PATCH", body: { multi_merchant_enabled: next, reason } }));
    } catch (err) {
      if (err instanceof ApiError && err.code === "step_up_required") onStepUp();
      else setError(err instanceof ApiError ? err.message : "Couldn't change the setting.");
    }
  }

  return (
    <section aria-labelledby="platform" className="flex flex-col gap-3 rounded-md border border-line p-5">
      <h2 id="platform" className="text-[17px]">Platform</h2>
      {error && <Notice tone="error">{error}</Notice>}
      {settings && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-[15px]">
              Additional merchants: <strong className="font-medium">{settings.multi_merchant_enabled ? "On" : "Off (STLL HAUS only)"}</strong>
            </span>
            <span className="text-[13px] text-ink-2">
              When on, other businesses can sign up and apply. Each still needs your approval before taking payments.
            </span>
          </div>
          <Button variant="secondary" onClick={toggle}>
            {settings.multi_merchant_enabled ? "Turn off" : "Turn on"}
          </Button>
        </div>
      )}
    </section>
  );
}

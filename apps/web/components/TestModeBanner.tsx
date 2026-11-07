"use client";

import { useEffect, useState } from "react";
import { api, type PublicConfig } from "@/lib/api";

/** Shown whenever payments are not live. It stays until a platform admin enables live payments. */
export function TestModeBanner() {
  const [config, setConfig] = useState<PublicConfig | null>(null);

  useEffect(() => {
    api<PublicConfig>("/v1/config").then(setConfig, () => setConfig(null));
  }, []);

  if (config?.live_payments_enabled) return null;
  return (
    <div role="note" className="bg-ink px-5 py-2 text-[13px] text-on-ink">
      Test mode: payments use the {config?.payments_provider === "akahu" ? "Akahu sandbox" : "mock provider"} and sample
      data. No real money moves.
    </div>
  );
}

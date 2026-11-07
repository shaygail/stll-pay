"use client";

import { useEffect, useState } from "react";
import { api, type PublicConfig } from "@/lib/api";

/**
 * Public platform flags. `multi_merchant_enabled` is false at launch (STLL HAUS only):
 * no public sign-up and no business switching in the interface.
 * Returns null until loaded.
 */
export function usePublicConfig(): PublicConfig | null {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  useEffect(() => {
    let alive = true;
    api<PublicConfig>("/v1/config").then(
      (c) => alive && setConfig(c),
      // If the API can't be reached, assume the safer launch mode.
      () =>
        alive &&
        setConfig({ environment: "unknown", payments_provider: "mock", live_payments_enabled: false, multi_merchant_enabled: false }),
    );
    return () => {
      alive = false;
    };
  }, []);
  return config;
}

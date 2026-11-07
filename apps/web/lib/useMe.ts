"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Me } from "@/lib/api";

/** Loads the signed-in user. Sends the browser to /login if the session isn't valid. */
export function useMe() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api<Me>("/v1/me").then(setMe, (err) => {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
    });
  }, [router]);

  useEffect(reload, [reload]);
  return { me, error, reload };
}

export function useLogout() {
  return useCallback(async () => {
    await api("/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    // A full page load, not client navigation: Next keeps visited pages alive in memory,
    // and on a shared device (such as a POS tablet) none of this session's data may remain.
    window.location.replace(`${window.location.origin}/login`);
  }, []);
}

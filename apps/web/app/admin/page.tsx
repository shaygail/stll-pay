"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PlatformSettings } from "@/components/PlatformSettings";
import { StepUp } from "@/components/StepUp";
import { Button, Notice, StatusChip } from "@/components/ui";
import { api, ApiError, type Merchant } from "@/lib/api";
import { useLogout } from "@/lib/useMe";

const NEXT: Record<string, string[]> = {
  pending_review: ["restricted", "active", "draft"],
  restricted: ["active", "suspended"],
  active: ["restricted", "suspended"],
  suspended: ["restricted", "active"],
  draft: [],
};

export default function AdminPage() {
  const [items, setItems] = useState<Merchant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsStepUp, setNeedsStepUp] = useState(false);
  const openStepUp = useCallback(() => setNeedsStepUp(true), []);

  const router = useRouter();
  const logout = useLogout();
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    api<{ items: Merchant[] }>("/v1/admin/merchants").then(
      (data) => {
        if (!alive) return;
        setItems(data.items);
        setError(null);
      },
      (err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.code === "step_up_required") setNeedsStepUp(true);
        else if (err instanceof ApiError && (err.status === 401 || err.status === 404)) router.replace("/dashboard");
        else setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
      },
    );
    return () => {
      alive = false;
    };
  }, [version, router]);

  async function changeStatus(m: Merchant, status: string) {
    const reason = window.prompt(`Reason for moving ${m.trading_name} to ${status}:`);
    if (!reason || reason.trim().length < 3) return;
    try {
      await api(`/v1/admin/merchants/${m.id}/status`, { body: { status, reason } });
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.code === "step_up_required") setNeedsStepUp(true);
      else setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
    }
  }

  return (
    <div data-theme="dark" className="flex min-h-dvh flex-col bg-bg text-ink">
      <header className="flex h-14 items-center justify-between border-b border-line px-5">
        <div className="flex items-center gap-4">
          <span className="wordmark">STLL PAY</span>
          <span className="text-[14px]">Platform admin</span>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/dashboard" className="text-[14px] text-ink-2">Dashboard</Link>
          <Button variant="text" onClick={logout}>Log out</Button>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-[1100px] flex-col gap-6 px-5 py-8">
        <h1 className="text-[28px]">Platform admin</h1>
        <PlatformSettings onStepUp={openStepUp} />
        <h2 className="text-[17px]">Merchants</h2>
        {error && <Notice tone="error">{error}</Notice>}
        {needsStepUp && (
          <StepUp
            onCancel={() => setNeedsStepUp(false)}
            onDone={() => {
              setNeedsStepUp(false);
              reload();
            }}
          />
        )}
        {items && (
          <div className="overflow-x-auto rounded-md border border-line">
            <table className="w-full min-w-[640px] border-collapse text-[14px]">
              <thead>
                <tr className="text-left text-[12px] text-ink-2">
                  <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Business</th>
                  <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Link name</th>
                  <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Status</th>
                  <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.id}>
                    <td className="border-b border-line px-3.5 py-2.5">{m.trading_name}</td>
                    <td className="border-b border-line px-3.5 py-2.5 font-mono text-[13px]">{m.slug}</td>
                    <td className="border-b border-line px-3.5 py-2.5"><StatusChip status={m.status} /></td>
                    <td className="border-b border-line px-3.5 py-1.5">
                      <div className="flex flex-wrap gap-2">
                        {(NEXT[m.status] ?? []).map((s) => (
                          <button
                            key={s}
                            onClick={() => changeStatus(m, s)}
                            className="h-9 min-h-0 rounded-md border border-line-strong px-3 text-[13px] capitalize"
                          >
                            {s.replace("_", " ")}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}

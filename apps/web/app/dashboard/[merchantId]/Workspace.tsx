"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PaymentsPanel } from "@/components/PaymentsPanel";
import { StepUp } from "@/components/StepUp";
import { TestModeBanner } from "@/components/TestModeBanner";
import { Button, Notice, StatusChip, Wordmark } from "@/components/ui";
import { api, ApiError, type Merchant } from "@/lib/api";
import { useLogout } from "@/lib/useMe";

type Member = { id: string; email: string; role: string };

async function loadWorkspace(merchantId: string) {
  const merchant = await api<Merchant>(`/v1/merchants/${merchantId}`);
  let members: Member[] | null = null;
  if (merchant.role === "owner" || merchant.role === "manager") {
    members = (await api<{ items: Member[] }>(`/v1/merchants/${merchantId}/members`)).items;
  }
  return { merchant, members };
}
const ROLES = ["owner", "manager", "cashier", "viewer"] as const;

const STATUS_HELP: Record<string, string> = {
  draft: "Add your details, then submit your business for review.",
  pending_review: "We're reviewing your business. Payments stay in test mode until it's approved.",
  restricted: "Your business is approved for testing only.",
  active: "Your business is approved.",
  suspended: "Your business is suspended. Contact support.",
};

export function MerchantWorkspace() {
  const { merchantId } = useParams<{ merchantId: string }>();
  const [merchant, setMerchant] = useState<Merchant | null>(null);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<(() => Promise<void>) | null>(null);
  const logout = useLogout();

  const router = useRouter();
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    loadWorkspace(merchantId).then(
      (data) => {
        if (!alive) return;
        setMerchant(data.merchant);
        setMembers(data.members);
      },
      (err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
      },
    );
    return () => {
      alive = false;
    };
  }, [merchantId, version, router]);

  async function attempt(action: () => Promise<void>) {
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      if (err instanceof ApiError && err.code === "step_up_required") {
        setPending(() => action);
        return;
      }
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
    }
  }

  function onAddMember(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const el = e.currentTarget;
    attempt(async () => {
      await api(`/v1/merchants/${merchantId}/members`, {
        body: { email: form.get("email"), role: form.get("role") },
      });
      el.reset();
      setNotice("Team member added.");
      reload();
    });
  }

  const isOwner = merchant?.role === "owner";

  return (
    <div className="flex min-h-dvh flex-col">
      <TestModeBanner />
      <header className="flex h-14 items-center justify-between border-b border-line px-5">
        <div className="flex items-center gap-4">
          <Wordmark href="/dashboard" />
          <Link href="/dashboard" className="text-[14px] text-ink-2">All businesses</Link>
        </div>
        <Button variant="text" onClick={logout}>Log out</Button>
      </header>
      <main className="mx-auto flex w-full max-w-[880px] flex-col gap-8 px-5 py-8">
        {error && <Notice tone="error">{error}</Notice>}
        {notice && <Notice>{notice}</Notice>}
        {pending && (
          <StepUp
            onCancel={() => setPending(null)}
            onDone={() => {
              const action = pending;
              setPending(null);
              attempt(action);
            }}
          />
        )}
        {!merchant && !error && <p role="status" className="text-ink-2">Loading…</p>}
        {merchant && (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="flex flex-col gap-1">
                <span className="eyebrow">Overview</span>
                <h1 className="text-[28px]">{merchant.trading_name}</h1>
                <span className="text-[14px] text-ink-2">
                  {merchant.legal_name} · pay.stllhaus.co/pay/{merchant.slug}
                </span>
              </div>
              <StatusChip status={merchant.status} />
            </div>

            <section className="flex flex-col gap-3 rounded-md border border-line p-5">
              <h2 className="text-[17px]">Account status</h2>
              <p className="text-[15px] text-ink-2">{STATUS_HELP[merchant.status]}</p>
              {isOwner && merchant.status === "draft" && (
                <Button
                  className="self-start"
                  onClick={() =>
                    attempt(async () => {
                      setMerchant(await api<Merchant>(`/v1/merchants/${merchantId}/submit`, { method: "POST" }));
                      setNotice("Submitted for review.");
                    })
                  }
                >
                  Submit for review
                </Button>
              )}
            </section>

            {merchant.status === "restricted" || merchant.status === "active" ? (
              <PaymentsPanel
                merchantId={merchant.id}
                canCreate={merchant.role === "owner" || merchant.role === "manager" || merchant.role === "cashier"}
                canManageBank={isOwner}
                onStepUp={(retry) => setPending(() => retry)}
              />
            ) : (
              <section className="flex flex-col gap-3 rounded-md border border-line p-5">
                <h2 className="text-[17px]">Payments</h2>
                <p className="text-[15px] text-ink-2">Payments open once STLL PAY has approved your business.</p>
              </section>
            )}

            {members && (
              <section aria-labelledby="team" className="flex flex-col gap-4">
                <h2 id="team" className="text-[17px]">Team</h2>
                <ul className="flex flex-col border-t border-line">
                  {members.map((m) => (
                    <li key={m.id} className="flex min-h-[52px] items-center justify-between gap-3 border-b border-line py-2">
                      <span className="text-[15px]">{m.email}</span>
                      <span className="text-[13px] capitalize text-ink-2">{m.role}</span>
                    </li>
                  ))}
                </ul>
                {isOwner && (
                  <form onSubmit={onAddMember} className="flex flex-wrap items-end gap-3" aria-label="Add team member">
                    <div className="flex min-w-[220px] flex-1 flex-col gap-2">
                      <label htmlFor="member-email" className="eyebrow">Email</label>
                      <input id="member-email" name="email" type="email" required className="h-12 rounded-md border border-line-strong px-3.5" />
                    </div>
                    <div className="flex flex-col gap-2">
                      <label htmlFor="member-role" className="eyebrow">Role</label>
                      <select id="member-role" name="role" defaultValue="cashier" className="h-12 rounded-md border border-line-strong px-3">
                        {ROLES.map((r) => (
                          <option key={r} value={r} className="capitalize">{r}</option>
                        ))}
                      </select>
                    </div>
                    <Button type="submit">Add</Button>
                  </form>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

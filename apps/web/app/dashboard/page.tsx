"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TestModeBanner } from "@/components/TestModeBanner";
import { Button, Field, Notice, StatusChip, Wordmark } from "@/components/ui";
import { api, ApiError, type Merchant } from "@/lib/api";
import { usePublicConfig } from "@/lib/useConfig";
import { useLogout, useMe } from "@/lib/useMe";

export default function DashboardHome() {
  const router = useRouter();
  const { me, error } = useMe();
  const logout = useLogout();
  const config = usePublicConfig();
  const singleMerchant = config !== null && !config.multi_merchant_enabled;

  // Launch mode: one business, so skip the list and open it directly.
  useEffect(() => {
    if (singleMerchant && me && me.memberships.length === 1) {
      router.replace(`/dashboard/${me.memberships[0].merchant_id}`);
    }
  }, [singleMerchant, me, router]);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setFormError(null);
    try {
      const m = await api<Merchant>("/v1/merchants", {
        body: {
          trading_name: form.get("trading_name"),
          legal_name: form.get("legal_name"),
          slug: String(form.get("slug") ?? "").toLowerCase(),
        },
      });
      router.push(`/dashboard/${m.id}`);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <TestModeBanner />
      <header className="flex h-14 items-center justify-between border-b border-line px-5">
        <Wordmark href="/dashboard" />
        <div className="flex items-center gap-2">
          {me?.is_platform_admin && (
            <Link href="/admin" className="tap inline-flex items-center px-2 text-[14px] text-ink-2">
              Admin
            </Link>
          )}
          <Button variant="text" onClick={logout}>
            Log out
          </Button>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-[720px] flex-col gap-8 px-5 py-8">
        {error && <Notice tone="error">{error}</Notice>}
        {!me && !error && <p role="status" className="text-ink-2">Loading…</p>}
        {me && (
          <>
            <div className="flex flex-col gap-1">
              <span className="eyebrow">Signed in as {me.email}</span>
              <h1 className="text-[28px]">Your businesses</h1>
            </div>
            {me.memberships.length > 0 ? (
              <ul className="flex flex-col border-t border-line">
                {me.memberships.map((m) => (
                  <li key={m.merchant_id} className="border-b border-line">
                    <Link
                      href={`/dashboard/${m.merchant_id}`}
                      className="flex min-h-[60px] items-center justify-between gap-3 py-3 no-underline"
                    >
                      <span className="flex flex-col">
                        <span className="text-[16px] text-ink">{m.trading_name}</span>
                        <span className="text-[13px] text-ink-2 capitalize">{m.role}</span>
                      </span>
                      <StatusChip status={m.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-2">
                {singleMerchant
                  ? "You're not on the STLL HAUS team yet. Ask your administrator to invite you."
                  : "You're not part of a business yet. Add yours below."}
              </p>
            )}
            {config?.multi_merchant_enabled && (
            <section aria-labelledby="new-business" className="flex flex-col gap-5 rounded-md border border-line p-5">
              <h2 id="new-business" className="text-[17px]">Add a business</h2>
              <form onSubmit={onCreate} className="flex flex-col gap-5" noValidate>
                <Field id="trading_name" name="trading_name" label="Trading name" required />
                <Field id="legal_name" name="legal_name" label="Legal name" hint="As registered, for example STLL HAUS Limited." required />
                <Field
                  id="slug"
                  name="slug"
                  label="Payment link name"
                  hint="Lower case letters, numbers and dashes. Your counter QR uses it: pay.stllhaus.co/pay/your-name"
                  required
                />
                {formError && <Notice tone="error">{formError}</Notice>}
                <Button type="submit" disabled={busy}>
                  {busy ? "Adding…" : "Add business"}
                </Button>
              </form>
            </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

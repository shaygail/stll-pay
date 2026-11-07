"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CheckoutShell, MerchantIdentity, TestBadge } from "@/components/checkout";
import { Button, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { parseNZDToCents } from "@/lib/money";

type Merchant = { trading_name: string; verified: boolean; accepting_payments: boolean; test_mode: boolean };

/** Counter QR: the customer types the amount shown at the counter. */
export function CounterPay() {
  const { slug } = useParams<{ slug: string }>();
  const qr = useSearchParams().get("qr");
  const router = useRouter();
  const [merchant, setMerchant] = useState<Merchant | null>(null);
  const [missing, setMissing] = useState(false);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api<Merchant>(`/v1/checkout/merchants/${encodeURIComponent(slug)}`).then(
      (m) => alive && setMerchant(m),
      () => alive && setMissing(true),
    );
    return () => {
      alive = false;
    };
  }, [slug]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const cents = parseNZDToCents(amount);
    if (cents === null) {
      setError("Enter an amount, like 12.50.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { checkout_token } = await api<{ checkout_token: string }>(
        `/v1/checkout/merchants/${encodeURIComponent(slug)}/requests`,
        { body: { amount_cents: cents, note: note.trim() || null, ...(qr ? { qr } : {}) } },
      );
      router.push(`/c/${checkout_token}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY. Check your connection.");
      setBusy(false);
    }
  }

  if (missing) {
    return (
      <CheckoutShell>
        <Notice tone="error">We couldn&apos;t find this business. Check the QR code, or ask staff.</Notice>
      </CheckoutShell>
    );
  }
  if (!merchant) {
    return (
      <CheckoutShell>
        <p role="status" className="text-ink-2">Loading…</p>
      </CheckoutShell>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <CheckoutShell
        footer={
          <>
            {error && <Notice tone="error">{error}</Notice>}
            <Button type="submit" disabled={busy || !merchant.accepting_payments}>
              {busy ? "One moment…" : "Continue"}
            </Button>
            <p className="text-center text-[12px] leading-relaxed text-ink-2">
              You approve the payment in your own bank. STLL PAY never sees your banking login.
            </p>
          </>
        }
      >
        <MerchantIdentity name={merchant.trading_name} verified={merchant.verified} />
        {!merchant.accepting_payments && <Notice tone="error">This business can&apos;t take payments right now.</Notice>}
        <div className="flex flex-col gap-2.5">
          <label htmlFor="amount" className="eyebrow">Amount (NZD)</label>
          <div className="flex items-baseline gap-1 border-b-[1.5px] border-ink pb-2">
            <span aria-hidden="true" className="text-[40px] font-medium text-ink-2">$</span>
            <input
              id="amount"
              inputMode="decimal"
              autoComplete="off"
              placeholder="0.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="amount w-full min-h-0 border-0 bg-transparent p-0 text-[48px] outline-none placeholder:text-ink-2"
            />
          </div>
          <span className="text-[13px] text-ink-2">Enter the amount shown at the counter.</span>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="note" className="eyebrow">What&apos;s it for? (optional)</label>
          <input
            id="note"
            value={note}
            maxLength={140}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. 2 coffees"
            className="h-12 rounded-md border border-line-strong px-3.5 placeholder:text-ink-2"
          />
        </div>
        {merchant.test_mode && <TestBadge />}
      </CheckoutShell>
    </form>
  );
}

"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { CheckoutShell, MerchantIdentity, StatusScreen, TestBadge } from "@/components/checkout";
import { Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatNZD, parseNZDToCents } from "@/lib/money";

type Link = {
  merchant: { trading_name: string; verified: boolean; test_mode: boolean };
  description: string;
  order_ref: string | null;
  amount_cents: number | null;
  state: "active" | "disabled" | "expired" | "used";
};

const CLOSED: Record<string, string> = {
  disabled: "This payment link has been switched off.",
  expired: "This payment link has expired.",
  used: "This payment link has already been paid.",
};

export function LinkPay() {
  const { code } = useParams<{ code: string }>();
  const router = useRouter();
  const [link, setLink] = useState<Link | null>(null);
  const [missing, setMissing] = useState(false);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api<Link>(`/v1/checkout/links/${encodeURIComponent(code)}`).then(
      (l) => alive && setLink(l),
      () => alive && setMissing(true),
    );
    return () => {
      alive = false;
    };
  }, [code]);

  async function onContinue(e: React.FormEvent) {
    e.preventDefault();
    let body: { amount_cents?: number } = {};
    if (link?.amount_cents == null) {
      const cents = parseNZDToCents(amount);
      if (cents === null) {
        setError("Enter an amount, like 12.50.");
        return;
      }
      body = { amount_cents: cents };
    }
    setBusy(true);
    setError(null);
    try {
      const { checkout_token } = await api<{ checkout_token: string }>(
        `/v1/checkout/links/${encodeURIComponent(code)}/requests`,
        { body },
      );
      router.push(`/c/${checkout_token}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
      setBusy(false);
    }
  }

  if (missing) {
    return (
      <CheckoutShell>
        <Notice tone="error">This payment link doesn&apos;t exist.</Notice>
      </CheckoutShell>
    );
  }
  if (!link) {
    return (
      <CheckoutShell>
        <p role="status" className="text-ink-2">Loading…</p>
      </CheckoutShell>
    );
  }
  if (link.state !== "active") {
    return (
      <CheckoutShell>
        <StatusScreen tone="neutral" icon="clock" label="Not available" title={CLOSED[link.state]}>
          <p className="text-[15px] text-ink-2">Nothing was paid. Contact {link.merchant.trading_name} if you still need to pay.</p>
        </StatusScreen>
      </CheckoutShell>
    );
  }

  return (
    <form onSubmit={onContinue} noValidate>
      <CheckoutShell
        footer={
          <>
            {error && <Notice tone="error">{error}</Notice>}
            <Button type="submit" disabled={busy}>{busy ? "One moment…" : "Continue"}</Button>
          </>
        }
      >
        <MerchantIdentity name={link.merchant.trading_name} verified={link.merchant.verified} />
        <div className="flex flex-col gap-1">
          <span className="eyebrow">{link.description}</span>
          {link.amount_cents != null && <p className="amount text-[44px] leading-none">{formatNZD(link.amount_cents)}</p>}
          {link.order_ref && <span className="text-[13px] text-ink-2">Ref {link.order_ref}</span>}
        </div>
        {link.amount_cents == null && (
          <Field id="amount" label="Amount (NZD)" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" />
        )}
        {link.merchant.test_mode && <TestBadge />}
      </CheckoutShell>
    </form>
  );
}

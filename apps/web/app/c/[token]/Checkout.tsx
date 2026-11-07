"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { CheckoutShell, MerchantIdentity, Reference, StatusScreen, TestBadge } from "@/components/checkout";
import { Button, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatNZD } from "@/lib/money";

type View = {
  merchant: { trading_name: string; verified: boolean; test_mode: boolean };
  amount_cents: number;
  description: string | null;
  reference: string;
  source: string;
  expires_at: string;
  state: "ready" | "waiting" | "processing" | "paid" | "failed" | "cancelled" | "expired" | "unconfirmed";
  can_pay: boolean;
  resume_url: string | null;
  paid_at: string | null;
};

const SETTLED = new Set(["paid", "failed", "cancelled", "expired", "unconfirmed", "ready"]);

/**
 * The checkout and return page. It only ever *reads* status: returning from the bank never
 * marks anything paid. When back from the bank it asks the API to check with the provider.
 */
export function Checkout({ returned }: { returned: boolean }) {
  const { token } = useParams<{ token: string }>();
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const polls = useRef(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      api<View>(`/v1/checkout/requests/${token}?refresh=${returned ? "true" : "false"}`).then(
        (v) => {
          if (!alive) return;
          setView(v);
          setError(null);
          // Keep checking while the bank is working: quickly at first, then slower.
          if (!SETTLED.has(v.state) || (returned && v.state === "ready" && polls.current < 3)) {
            polls.current += 1;
            timer = setTimeout(load, polls.current < 15 ? 2000 : 6000);
          }
        },
        (err) => {
          if (!alive) return;
          setError(err instanceof ApiError ? err.message : "You're offline. We'll keep trying.");
          timer = setTimeout(load, 5000);
        },
      );
    };
    load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [token, returned]);

  async function onPay() {
    setBusy(true);
    setError(null);
    try {
      const { authorisation_url } = await api<{ authorisation_url: string }>(`/v1/checkout/requests/${token}/pay`, {
        method: "POST",
      });
      window.location.assign(authorisation_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY. Check your connection.");
      setBusy(false);
    }
  }

  if (!view) {
    return (
      <CheckoutShell>
        {error ? <Notice tone="error">{error}</Notice> : <p role="status" className="text-ink-2">Loading…</p>}
      </CheckoutShell>
    );
  }

  const amount = formatNZD(view.amount_cents);
  const name = view.merchant.trading_name;

  if (view.state === "paid") {
    return (
      <CheckoutShell>
        <StatusScreen tone="ok" icon="check" label="Paid · confirmed by your bank" title={amount}>
          <p className="text-[16px] text-ink-2">to {name}</p>
          <dl className="flex flex-col border-t border-line text-[14px]">
            {view.paid_at && (
              <div className="flex justify-between border-b border-line py-3">
                <dt className="text-ink-2">Confirmed</dt>
                <dd>{new Date(view.paid_at).toLocaleString("en-NZ", { dateStyle: "medium", timeStyle: "short" })}</dd>
              </div>
            )}
            <div className="flex justify-between border-b border-line py-3">
              <dt className="text-ink-2">Reference</dt>
              <dd className="font-mono">{view.reference}</dd>
            </div>
          </dl>
          <p className="text-[14px] text-ink-2">{name} can see this payment. You don&apos;t need to show this screen.</p>
          {view.merchant.test_mode && <TestBadge />}
        </StatusScreen>
      </CheckoutShell>
    );
  }

  if (view.state === "processing" || (view.state === "waiting" && returned)) {
    return (
      <CheckoutShell>
        <StatusScreen tone="neutral" icon="progress" label="Processing · not paid yet" title={`Your bank is processing ${amount}`}>
          <p role="status" className="text-[15px] text-ink-2">
            This usually takes a few seconds, sometimes a few minutes. This page updates by itself.
          </p>
          <Notice>
            <strong className="font-semibold">Please don&apos;t pay again.</strong> {name} can see this payment. You can close
            this page; the result still reaches them.
          </Notice>
          <Reference value={view.reference} />
        </StatusScreen>
      </CheckoutShell>
    );
  }

  if (view.state === "unconfirmed") {
    return (
      <CheckoutShell>
        <StatusScreen tone="strong" icon="warn" label="Unconfirmed · being checked" title="We can't confirm this payment yet">
          <p className="text-[15px] text-ink-2">Your bank didn&apos;t give a clear answer, so {amount} may or may not have been sent.</p>
          <div className="flex flex-col gap-1.5 rounded-md bg-ink p-4 text-on-ink">
            <strong className="text-[15px] font-semibold">Please don&apos;t pay again.</strong>
            <span className="text-[13px]">Show this reference to staff. {name} will check with the bank and sort it out with you.</span>
            <span className="pt-1.5 font-mono text-[22px] tracking-[0.06em]">{view.reference}</span>
          </div>
        </StatusScreen>
      </CheckoutShell>
    );
  }

  if (view.state === "expired") {
    return (
      <CheckoutShell>
        <StatusScreen tone="neutral" icon="clock" label="Expired" title="This payment request has expired">
          <p className="text-[15px] text-ink-2">
            For your safety, each payment request only works for a short time. Nothing was paid.
            {view.source === "dynamic_qr" ? ` Ask staff at ${name} for a new code.` : " Start again to pay."}
          </p>
        </StatusScreen>
      </CheckoutShell>
    );
  }

  const payButton = (
    <Button onClick={onPay} disabled={busy || !view.can_pay}>
      {busy ? "Opening your bank…" : `Pay ${amount} with my bank`}
    </Button>
  );

  if (view.state === "failed" || view.state === "cancelled") {
    const failed = view.state === "failed";
    return (
      <CheckoutShell footer={<>{error && <Notice tone="error">{error}</Notice>}{payButton}</>}>
        <StatusScreen
          tone={failed ? "bad" : "neutral"}
          icon={failed ? "x" : "back"}
          label={failed ? "Not paid" : "Cancelled"}
          title={failed ? "Your payment didn't go through" : "You cancelled the payment"}
        >
          <p className="text-[15px] text-ink-2">
            {failed
              ? `Your bank declined the ${amount} payment to ${name}, so no money was sent. Banks don't share the reason; a common one is a daily limit or not enough funds.`
              : `No money was sent. You can pay ${name} now, or ask staff to pay another way.`}
          </p>
          <Reference value={view.reference} />
        </StatusScreen>
      </CheckoutShell>
    );
  }

  // ready, or waiting (customer came back to this tab before finishing at the bank)
  return (
    <CheckoutShell
      right={<span className="text-[12px] text-ink-2">Expires {new Date(view.expires_at).toLocaleTimeString("en-NZ", { timeStyle: "short" })}</span>}
      footer={
        <>
          {error && <Notice tone="error">{error}</Notice>}
          {view.state === "waiting" && view.resume_url ? (
            <a href={view.resume_url} className="tap flex h-[52px] items-center justify-center rounded-md bg-ink text-[16px] font-medium text-on-ink no-underline">
              Continue at your bank
            </a>
          ) : (
            payButton
          )}
        </>
      }
    >
      <MerchantIdentity name={name} verified={view.merchant.verified} />
      <div className="flex flex-col gap-1">
        <span className="eyebrow">{view.description ?? "Amount"}</span>
        <p className="amount text-[44px] leading-none">{amount}</p>
      </div>
      <section aria-labelledby="how" className="flex flex-col gap-3">
        <h2 id="how" className="eyebrow">What happens next</h2>
        <ol className="flex flex-col gap-2.5 text-[14px] leading-snug">
          {[
            "Choose your bank on Akahu's secure page.",
            `Approve ${amount} in your banking app.`,
            "Come back here. We show “Paid” only once your bank confirms it.",
          ].map((step, i) => (
            <li key={step} className="flex gap-3">
              <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-sm border border-line-strong text-[12px]">{i + 1}</span>
              {step}
            </li>
          ))}
        </ol>
      </section>
      <aside className="rounded-md bg-surface p-3.5 text-[12px] leading-relaxed text-ink-2">
        Akahu is New Zealand&apos;s open finance platform. It connects to your bank through regulated open banking. Your
        banking login stays with your bank, and STLL PAY never sees it.
      </aside>
      <Reference value={view.reference} />
      {view.merchant.test_mode && <TestBadge />}
    </CheckoutShell>
  );
}

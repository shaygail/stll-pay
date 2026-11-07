"use client";

import { useCallback, useEffect, useState } from "react";
import { renderSVG } from "uqr";
import { Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatNZD, parseNZDToCents } from "@/lib/money";

type Req = {
  id: string;
  reference: string;
  status: string;
  amount_cents: number;
  description: string | null;
  source: string;
  checkout_url: string;
  expires_at: string;
  created_at: string | null;
};
type Summary = {
  test_mode: boolean;
  received: { count: number; amount_cents: number };
  processing: { count: number; amount_cents: number };
  needs_review: { count: number };
  not_paid: { count: number };
};
type Account = { id: string; holder_name: string; last4: string; status: string };

const STATUS: Record<string, { label: string; tone: string }> = {
  open: { label: "Open", tone: "border-line-strong text-ink-2" },
  paid: { label: "Paid", tone: "border-sage-ink text-sage-ink" },
  cancelled: { label: "Cancelled", tone: "border-line-strong text-ink-2" },
  expired: { label: "Expired", tone: "border-line-strong text-ink-2" },
  requires_review: { label: "Needs review", tone: "border-ink text-ink" },
};

/** Take a payment from the dashboard (dynamic QR), see results and the bank account. */
export function PaymentsPanel({
  merchantId,
  canCreate,
  canManageBank,
  onStepUp,
}: {
  merchantId: string;
  canCreate: boolean;
  canManageBank: boolean;
  onStepUp: (retry: () => Promise<void>) => void;
}) {
  const [items, setItems] = useState<Req[] | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [current, setCurrent] = useState<Req | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api<{ items: Req[] }>(`/v1/merchants/${merchantId}/payment-requests?limit=20`),
      api<Summary>(`/v1/merchants/${merchantId}/summary`),
      api<{ items: Account[] }>(`/v1/merchants/${merchantId}/bank-accounts`),
    ]).then(
      ([r, s, a]) => {
        if (!alive) return;
        setItems(r.items);
        setSummary(s);
        setAccounts(a.items);
        setCurrent((c) => (c ? (r.items.find((x) => x.id === c.id) ?? c) : c));
      },
      (err) => alive && setError(err instanceof ApiError ? err.message : "Couldn't load payments."),
    );
    return () => {
      alive = false;
    };
  }, [merchantId, version]);

  // While a request is on screen and still open, refresh its status.
  useEffect(() => {
    if (!current || current.status !== "open") return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [current, reload]);

  async function onCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const cents = parseNZDToCents(String(form.get("amount") ?? ""));
    if (cents === null) {
      setError("Enter an amount, like 12.50.");
      return;
    }
    setError(null);
    try {
      const req = await api<Req>(`/v1/merchants/${merchantId}/payment-requests`, {
        body: { amount_cents: cents, description: String(form.get("description") ?? "") || null },
      });
      setCurrent(req);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the payment request.");
    }
  }

  function onAddAccount(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const el = e.currentTarget;
    const run = async () => {
      await api(`/v1/merchants/${merchantId}/bank-accounts`, {
        body: { account_number: form.get("account_number"), holder_name: form.get("holder_name") },
      });
      el.reset();
      reload();
    };
    run().catch((err) => {
      if (err instanceof ApiError && err.code === "step_up_required") onStepUp(run);
      else setError(err instanceof ApiError ? err.message : "Couldn't add the account.");
    });
  }

  const verified = accounts.find((a) => a.status === "verified");
  const pending = accounts.filter((a) => a.status === "pending");

  return (
    <section aria-labelledby="payments" className="flex flex-col gap-5">
      <h2 id="payments" className="text-[17px]">Payments</h2>
      {error && <Notice tone="error">{error}</Notice>}

      {summary && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Tile label="Received today" value={formatNZD(summary.received.amount_cents)} sub={`${summary.received.count} confirmed by bank`} />
          <Tile label="Processing" value={formatNZD(summary.processing.amount_cents)} sub={`${summary.processing.count} not received yet`} />
          <Tile label="Not paid" value={String(summary.not_paid.count)} sub="cancelled or expired" />
          <Tile label="Needs review" value={String(summary.needs_review.count)} sub="bank result unclear" strong={summary.needs_review.count > 0} />
        </div>
      )}

      <div className="flex flex-col gap-3 rounded-md border border-line p-5">
        <h3 className="text-[15px] font-medium">Receiving bank account</h3>
        {verified ? (
          <p className="text-[14px]">
            {verified.holder_name} ··{verified.last4} <span className="text-sage-ink">· verified</span>
          </p>
        ) : (
          <p className="text-[14px] text-ink-2">No verified account yet. Payments can&apos;t start until one is verified.</p>
        )}
        {pending.map((a) => (
          <p key={a.id} className="text-[13px] text-ink-2">
            {a.holder_name} ··{a.last4} · waiting for verification by STLL PAY
          </p>
        ))}
        {canManageBank && (
          <form onSubmit={onAddAccount} className="flex flex-col gap-3" aria-label="Add bank account" noValidate>
            <Field id="account_number" name="account_number" label="Account number" placeholder="12-3456-7890123-00" required />
            <Field id="holder_name" name="holder_name" label="Account name" required />
            <Button type="submit" variant="secondary" className="self-start">Add account</Button>
            <p className="text-[12px] text-ink-2">
              A new account replaces the old one only after it&apos;s verified. Payments already started keep going to the old account.
            </p>
          </form>
        )}
      </div>

      {canCreate && (
        <div className="grid gap-5 rounded-md border border-line p-5 md:grid-cols-2">
          <form onSubmit={onCreate} className="flex flex-col gap-4" aria-label="New payment request" noValidate>
            <h3 className="text-[15px] font-medium">Take a payment</h3>
            <Field id="amount" name="amount" label="Amount (NZD)" inputMode="decimal" placeholder="0.00" required />
            <Field id="description" name="description" label="Description (optional)" maxLength={140} />
            <Button type="submit" disabled={!verified}>Show QR code</Button>
          </form>
          {current && <RequestQr req={current} />}
        </div>
      )}

      {items && items.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[560px] border-collapse text-[14px]">
            <thead>
              <tr className="text-left text-[12px] text-ink-2">
                <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Created</th>
                <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Reference</th>
                <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Source</th>
                <th scope="col" className="border-b border-line px-3.5 py-2.5 text-right font-medium">Amount</th>
                <th scope="col" className="border-b border-line px-3.5 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id}>
                  <td className="border-b border-line px-3.5 py-2.5 text-ink-2">
                    {r.created_at ? new Date(r.created_at).toLocaleTimeString("en-NZ", { timeStyle: "short" }) : ""}
                  </td>
                  <td className="border-b border-line px-3.5 py-2.5 font-mono text-[13px]">{r.reference}</td>
                  <td className="border-b border-line px-3.5 py-2.5 text-ink-2">{r.source.replace("_", " ")}</td>
                  <td className="amount border-b border-line px-3.5 py-2.5 text-right">{formatNZD(r.amount_cents)}</td>
                  <td className="border-b border-line px-3.5 py-2.5">
                    <span className={`whitespace-nowrap rounded-sm border px-2 py-0.5 text-[12px] ${STATUS[r.status]?.tone ?? ""}`}>
                      {STATUS[r.status]?.label ?? r.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Tile({ label, value, sub, strong }: { label: string; value: string; sub: string; strong?: boolean }) {
  return (
    <div className={`flex flex-col gap-1 rounded-md p-3.5 ${strong ? "border-[1.5px] border-ink" : "border border-line"}`}>
      <span className="text-[12px] text-ink-2">{label}</span>
      <span className="amount text-[20px]">{value}</span>
      <span className="text-[12px] text-ink-2">{sub}</span>
    </div>
  );
}

function RequestQr({ req }: { req: Req }) {
  const label = STATUS[req.status]?.label ?? req.status;
  return (
    <div className="flex flex-col items-center gap-3">
      <span className="amount text-[32px]">{formatNZD(req.amount_cents)}</span>
      <div
        role="img"
        aria-label={`QR code to pay ${formatNZD(req.amount_cents)}`}
        className="w-[220px] rounded-md bg-white p-3"
        dangerouslySetInnerHTML={{ __html: renderSVG(req.checkout_url) }}
      />
      <p role="status" className="text-[14px]" data-testid="request-status">
        {req.status === "open" ? "Waiting for customer" : label}
      </p>
      <a href={new URL(req.checkout_url).pathname} className="text-[13px] text-ink-2 underline" target="_blank" rel="noreferrer">
        Open the checkout link
      </a>
    </div>
  );
}

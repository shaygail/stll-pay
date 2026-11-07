"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatNZD, parseNZDToCents } from "@/lib/money";

type Link = {
  id: string;
  code: string;
  amount_cents: number | null;
  description: string;
  order_ref: string | null;
  expires_at: string | null;
  max_uses: number | null;
  paid_count: number;
  active: boolean;
  url: string;
};

/** Payment links to send by message or email, for example invoices or deposits. */
export function LinksPanel({ merchantId, canCreate }: { merchantId: string; canCreate: boolean }) {
  const [items, setItems] = useState<Link[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    api<{ items: Link[] }>(`/v1/merchants/${merchantId}/payment-links`).then(
      (r) => alive && setItems(r.items),
      (err) => alive && setError(err instanceof ApiError ? err.message : "Couldn't load payment links."),
    );
    return () => {
      alive = false;
    };
  }, [merchantId, version]);

  async function onCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const el = e.currentTarget;
    const amountText = String(form.get("amount") ?? "").trim();
    const cents = amountText ? parseNZDToCents(amountText) : null;
    if (amountText && cents === null) {
      setError("Enter an amount like 45.00, or leave it empty to let the customer choose.");
      return;
    }
    const days = Number(form.get("days") || 0);
    setError(null);
    try {
      await api(`/v1/merchants/${merchantId}/payment-links`, {
        body: {
          amount_cents: cents,
          description: form.get("description"),
          order_ref: String(form.get("order_ref") ?? "") || null,
          expires_at: days > 0 ? new Date(Date.now() + days * 86_400_000).toISOString() : null,
          max_uses: form.get("single") ? 1 : null,
        },
      });
      el.reset();
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the link.");
    }
  }

  async function disable(link: Link) {
    try {
      await api(`/v1/merchants/${merchantId}/payment-links/${link.id}/disable`, { method: "POST" });
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't turn the link off.");
    }
  }

  async function copy(link: Link) {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(link.id);
    } catch {
      setCopied(null);
    }
  }

  return (
    <section aria-labelledby="links" className="flex flex-col gap-4 rounded-md border border-line p-5">
      <div className="flex flex-col gap-1">
        <h2 id="links" className="text-[17px]">Payment links</h2>
        <p className="text-[14px] text-ink-2">Send a link for an invoice, deposit or order. Customers pay from their bank.</p>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {canCreate && (
        <form onSubmit={onCreate} className="grid gap-4 sm:grid-cols-2" aria-label="New payment link" noValidate>
          <Field id="link-description" name="description" label="What it's for" maxLength={140} required />
          <Field id="link-amount" name="amount" label="Amount (NZD, optional)" inputMode="decimal" placeholder="Empty: customer enters it" />
          <Field id="link-ref" name="order_ref" label="Invoice or order ref (optional)" maxLength={60} />
          <Field id="link-days" name="days" label="Expires after (days, optional)" inputMode="numeric" placeholder="e.g. 14" />
          <label className="flex items-center gap-2 text-[14px]">
            <input type="checkbox" name="single" defaultChecked className="h-5 w-5 min-h-0" /> Can only be paid once
          </label>
          <Button type="submit" className="sm:justify-self-end">Create link</Button>
        </form>
      )}
      {items && items.length > 0 && (
        <ul className="flex flex-col border-t border-line">
          {items.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-3">
              <div className="flex min-w-0 flex-col">
                <span className="text-[15px]">
                  {l.description} · {l.amount_cents != null ? formatNZD(l.amount_cents) : "customer enters amount"}
                </span>
                <span className="text-[12px] text-ink-2">
                  {l.active ? "Active" : "Off"}
                  {l.order_ref && ` · ref ${l.order_ref}`}
                  {` · paid ${l.paid_count}${l.max_uses ? ` of ${l.max_uses}` : ""}`}
                  {l.expires_at && ` · expires ${new Date(l.expires_at).toLocaleDateString("en-NZ")}`}
                </span>
              </div>
              <div className="flex gap-2">
                <button onClick={() => copy(l)} className="h-9 min-h-0 rounded-md border border-line-strong px-3 text-[13px]">
                  {copied === l.id ? "Copied" : "Copy link"}
                </button>
                {l.active && canCreate && (
                  <button onClick={() => disable(l)} className="h-9 min-h-0 rounded-md border border-line-strong px-3 text-[13px]">
                    Turn off
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

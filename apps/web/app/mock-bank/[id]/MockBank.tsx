"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatNZD } from "@/lib/money";

type Payment = { amount_cents: number; payee_name: string; payee_reference: string | null; status: string };

/**
 * Development only: stands in for Akahu's hosted page and the customer's bank.
 * The API refuses these endpoints in production. Deliberately looks different from
 * STLL PAY so no one mistakes it for a real bank.
 */
export function MockBank() {
  const { id } = useParams<{ id: string }>();
  const [payment, setPayment] = useState<Payment | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<Payment>(`/v1/mock-bank/${id}`).then(
      (p) => alive && setPayment(p),
      () => alive && setError("This test payment doesn't exist."),
    );
    return () => {
      alive = false;
    };
  }, [id]);

  async function act(action: "approve" | "cancel") {
    try {
      const { redirect_uri } = await api<{ redirect_uri: string }>(`/v1/mock-bank/${id}/${action}`, { method: "POST" });
      window.location.assign(redirect_uri);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Mock bank unavailable.");
    }
  }

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-[440px] flex-col gap-6 px-5 py-8 font-mono">
      <div className="rounded-md border-2 border-dashed border-danger p-3 text-[13px] text-danger">
        MOCK BANK · test only · no real money · not Akahu
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {payment && (
        <>
          <h1 className="text-[22px]">Approve this payment?</h1>
          <dl className="flex flex-col gap-2 text-[14px]">
            <div className="flex justify-between"><dt>To</dt><dd>{payment.payee_name}</dd></div>
            <div className="flex justify-between"><dt>Amount</dt><dd>{formatNZD(payment.amount_cents)}</dd></div>
            <div className="flex justify-between"><dt>Reference</dt><dd>{payment.payee_reference}</dd></div>
            <div className="flex justify-between"><dt>Status</dt><dd>{payment.status}</dd></div>
          </dl>
          {payment.status === "READY" ? (
            <div className="flex flex-col gap-2">
              <Button onClick={() => act("approve")}>Approve payment</Button>
              <Button variant="secondary" onClick={() => act("cancel")}>Cancel</Button>
            </div>
          ) : (
            <p className="text-[14px]">This payment is no longer waiting for approval.</p>
          )}
          <p className="text-[12px] text-ink-2">
            Test outcomes follow Akahu Demo Bank amounts, for example $5.15 is declined, $5.20 settles after 30 s, $5.04 is
            an unclear bank result.
          </p>
        </>
      )}
    </div>
  );
}

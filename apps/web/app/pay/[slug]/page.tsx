import { Suspense } from "react";
import { CounterPay } from "./CounterPay";

export const metadata = { title: "Pay", robots: { index: false } };

export default function Page() {
  return (
    <Suspense fallback={<p role="status" className="p-5 text-ink-2">Loading…</p>}>
      <CounterPay />
    </Suspense>
  );
}

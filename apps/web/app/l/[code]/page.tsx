import { Suspense } from "react";
import { LinkPay } from "./LinkPay";

export const metadata = { title: "Pay", robots: { index: false } };

export default function Page() {
  return (
    <Suspense fallback={<p role="status" className="p-5 text-ink-2">Loading…</p>}>
      <LinkPay />
    </Suspense>
  );
}

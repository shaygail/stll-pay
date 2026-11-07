import { Suspense } from "react";
import { Checkout } from "./Checkout";

export const metadata = { title: "Pay", robots: { index: false } };

export default function CheckoutPage() {
  return (
    <Suspense fallback={<p role="status" className="p-5 text-ink-2">Loading…</p>}>
      <Checkout returned={false} />
    </Suspense>
  );
}

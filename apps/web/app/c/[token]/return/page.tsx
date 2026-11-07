import { Suspense } from "react";
import { Checkout } from "../Checkout";

export const metadata = { title: "Payment status", robots: { index: false } };

export default function ReturnPage() {
  return (
    <Suspense fallback={<p role="status" className="p-5 text-ink-2">Checking with your bank…</p>}>
      <Checkout returned />
    </Suspense>
  );
}

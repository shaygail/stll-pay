import { Suspense } from "react";
import { MockBank } from "./MockBank";

export const metadata = { title: "Mock bank", robots: { index: false } };

export default function Page() {
  return (
    <Suspense fallback={<p role="status" className="p-5 text-ink-2">Loading…</p>}>
      <MockBank />
    </Suspense>
  );
}

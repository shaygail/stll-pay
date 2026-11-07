import { Suspense } from "react";
import { MerchantWorkspace } from "./Workspace";

export default function MerchantPage() {
  return (
    <Suspense fallback={<p role="status" className="p-5 text-ink-2">Loading…</p>}>
      <MerchantWorkspace />
    </Suspense>
  );
}

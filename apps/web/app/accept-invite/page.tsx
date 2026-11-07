import { Suspense } from "react";
import { AuthShell } from "@/components/ui";
import { AcceptInvite } from "./AcceptInvite";

export const metadata = { title: "Join your team", robots: { index: false } };

export default function AcceptInvitePage() {
  return (
    <AuthShell title="Set your password" eyebrow="You've been invited">
      <Suspense fallback={<p className="text-ink-2">Loading…</p>}>
        <AcceptInvite />
      </Suspense>
    </AuthShell>
  );
}

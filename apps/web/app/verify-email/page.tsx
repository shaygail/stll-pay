import { Suspense } from "react";
import { AuthShell } from "@/components/ui";
import { VerifyEmail } from "./VerifyEmail";

export default function VerifyEmailPage() {
  return (
    <AuthShell title="Verifying your email" eyebrow="Create account">
      <Suspense fallback={<p className="text-ink-2">Checking your link…</p>}>
        <VerifyEmail />
      </Suspense>
    </AuthShell>
  );
}

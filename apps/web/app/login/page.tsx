"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { renderSVG } from "uqr";
import { AuthShell, Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

type Step = "password" | "verify" | "setup";

export default function LoginPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("password");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY. Check your connection.");
    } finally {
      setBusy(false);
    }
  }

  function onPassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    run(async () => {
      const res = await api<{ mfa_enrolled: boolean }>("/v1/auth/login", {
        body: { email: form.get("email"), password: form.get("password") },
      });
      if (res.mfa_enrolled) {
        setStep("verify");
      } else {
        setSetup(await api("/v1/auth/mfa/setup", { method: "POST" }));
        setStep("setup");
      }
    });
  }

  function onCode(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code") ?? "");
    run(async () => {
      await api(step === "setup" ? "/v1/auth/mfa/enable" : "/v1/auth/mfa/verify", { body: { code } });
      // Next keeps visited pages' state; reset so the setup key isn't kept around and the
      // next visit to /login starts at the password step.
      setSetup(null);
      setStep("password");
      router.replace("/dashboard");
    });
  }

  if (step === "password") {
    return (
      <AuthShell title="Log in" eyebrow="STLL PAY for business">
        <form onSubmit={onPassword} className="flex flex-col gap-5" noValidate>
          <Field id="email" name="email" type="email" label="Email" autoComplete="email" required />
          <Field id="password" name="password" type="password" label="Password" autoComplete="current-password" required />
          {error && <Notice tone="error">{error}</Notice>}
          <Button type="submit" disabled={busy}>
            {busy ? "Checking…" : "Continue"}
          </Button>
          <p className="text-[14px] text-ink-2">
            New to STLL PAY? <Link href="/signup" className="text-ink underline">Create an account</Link>
          </p>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={step === "setup" ? "Set up two-step verification" : "Enter your code"}
      eyebrow="Two-step verification"
    >
      {step === "setup" && setup && (
        <div className="flex flex-col gap-4">
          <p className="text-[15px] text-ink-2">
            STLL PAY needs a second step every time you log in. Scan this with an authenticator app,
            such as Google Authenticator, 1Password or Microsoft Authenticator.
          </p>
          <div
            role="img"
            aria-label="QR code for your authenticator app"
            className="w-[200px] rounded-md bg-white p-3"
            dangerouslySetInnerHTML={{ __html: renderSVG(setup.otpauth_uri) }}
          />
          <p className="text-[13px] text-ink-2">
            Can&apos;t scan? Enter this key: <span data-testid="mfa-secret" className="font-mono text-ink break-all">{setup.secret}</span>
          </p>
        </div>
      )}
      <form onSubmit={onCode} className="flex flex-col gap-5" noValidate>
        <Field
          id="code"
          name="code"
          label="6-digit code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          maxLength={6}
          required
        />
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" disabled={busy}>
          {busy ? "Checking…" : step === "setup" ? "Turn on and continue" : "Log in"}
        </Button>
      </form>
    </AuthShell>
  );
}

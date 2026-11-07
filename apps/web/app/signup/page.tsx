"use client";

import Link from "next/link";
import { useState } from "react";
import { AuthShell, Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export default function SignupPage() {
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/signup", {
        body: { email: form.get("email"), password: form.get("password") },
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY. Check your connection.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthShell title="Check your email" eyebrow="Create account">
        <p className="text-ink-2">We&apos;ve sent you a link to verify your email. It works for 24 hours.</p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Create a business account" eyebrow="Create account">
      <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <Field id="email" name="email" type="email" label="Email" autoComplete="email" required />
        <Field
          id="password"
          name="password"
          type="password"
          label="Password"
          autoComplete="new-password"
          hint="At least 12 characters, mixing letters, numbers or symbols."
          required
        />
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" disabled={busy}>
          {busy ? "Creating account…" : "Create account"}
        </Button>
        <p className="text-[14px] text-ink-2">
          Already have an account? <Link href="/login" className="text-ink underline">Log in</Link>
        </p>
      </form>
    </AuthShell>
  );
}

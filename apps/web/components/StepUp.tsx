"use client";

import { useState } from "react";
import { Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

/** Asks for an authenticator code before a sensitive action, then retries it. */
export function StepUp({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code") ?? "");
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/step-up", { body: { code } });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4 rounded-md border border-ink p-4" aria-label="Confirm it's you">
      <p className="text-[14px]">Confirm it&apos;s you with your authenticator code.</p>
      <Field id="stepup-code" name="code" label="6-digit code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required />
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>Confirm</Button>
        <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

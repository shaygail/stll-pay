"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button, ButtonLink, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export function AcceptInvite() {
  const token = useSearchParams().get("token");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(token ? null : "This invitation link is incomplete.");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    if (form.get("password") !== form.get("confirm")) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/accept-invite", { body: { token, password: form.get("password") } });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="flex flex-col gap-5">
        <Notice>Your password is set. Next, log in and set up two-step verification.</Notice>
        <ButtonLink href="/login">Log in</ButtonLink>
      </div>
    );
  }
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
      <Field
        id="password"
        name="password"
        type="password"
        label="New password"
        autoComplete="new-password"
        hint="At least 12 characters, mixing letters, numbers or symbols."
        required
      />
      <Field id="confirm" name="confirm" type="password" label="Type it again" autoComplete="new-password" required />
      {error && <Notice tone="error">{error}</Notice>}
      <Button type="submit" disabled={busy || !token}>
        {busy ? "Saving…" : "Set password"}
      </Button>
    </form>
  );
}

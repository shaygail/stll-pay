"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ButtonLink, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export function VerifyEmail() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"working" | "ok" | "error">(token ? "working" : "error");
  const [message, setMessage] = useState(token ? "" : "This link is missing its code.");
  const sent = useRef(false);

  useEffect(() => {
    if (!token || sent.current) return;
    sent.current = true;
    api("/v1/auth/verify-email", { body: { token } }).then(
      () => setState("ok"),
      (err) => {
        setState("error");
        setMessage(err instanceof ApiError ? err.message : "Couldn't reach STLL PAY.");
      },
    );
  }, [token]);

  if (state === "working") return <p role="status" className="text-ink-2">Checking your link…</p>;
  if (state === "error") return <Notice tone="error">{message}</Notice>;
  return (
    <div className="flex flex-col gap-5">
      <Notice>Your email is verified.</Notice>
      <ButtonLink href="/login">Log in</ButtonLink>
    </div>
  );
}

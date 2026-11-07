import type { ReactNode } from "react";

/** Checkout building blocks matching the Phase 2 designs (phone first). */

export function CheckoutShell({ right, children, footer }: { right?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-[440px] flex-col">
      <header className="flex h-14 items-center justify-between border-b border-line px-5">
        <span className="wordmark">STLL PAY</span>
        {right ?? (
          <span className="flex items-center gap-1.5 text-[12px] text-ink-2">
            <LockIcon />
            Secure bank payment
          </span>
        )}
      </header>
      <main className="flex flex-1 flex-col gap-6 px-5 pt-7">{children}</main>
      {footer && <footer className="flex flex-col gap-2 px-5 pb-7 pt-4">{footer}</footer>}
    </div>
  );
}

export function TestBadge() {
  return (
    <p role="note" className="rounded-sm border border-line-strong px-2 py-1 text-[12px] text-ink-2">
      Test mode: no real money moves.
    </p>
  );
}

export function MerchantIdentity({ name, verified }: { name: string; verified: boolean }) {
  return (
    <section aria-label="Who you are paying" className="flex items-center gap-3.5">
      <div aria-hidden="true" className="flex h-14 w-14 items-center justify-center rounded-md bg-ink text-[13px] font-medium tracking-[0.2em] text-on-ink">
        {name.slice(0, 4).toUpperCase()}
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="eyebrow">Paying</span>
        <h1 className="text-[22px] leading-tight">{name}</h1>
        {verified && (
          <span className="flex items-center gap-1.5 text-[13px] text-sage-ink">
            <CheckCircle /> Verified business
          </span>
        )}
      </div>
    </section>
  );
}

type Tone = "ok" | "bad" | "neutral" | "strong";

export function StatusScreen({
  tone,
  label,
  title,
  children,
  icon,
}: {
  tone: Tone;
  label: string;
  title: string;
  icon: "check" | "x" | "back" | "clock" | "warn" | "progress";
  children?: ReactNode;
}) {
  const tile = {
    ok: "bg-sage-soft text-sage-ink",
    bad: "bg-[rgba(163,71,63,0.1)] text-danger",
    neutral: "bg-surface text-ink-2",
    strong: "border-[1.5px] border-ink text-ink",
  }[tone];
  const labelColour = { ok: "text-sage-ink", bad: "text-danger", neutral: "text-ink-2", strong: "text-ink" }[tone];
  return (
    <div className="flex flex-col gap-5 pt-4">
      <span aria-hidden="true" className={`flex h-14 w-14 items-center justify-center rounded-md ${tile}`}>
        <Icon name={icon} />
      </span>
      <span className={`eyebrow ${labelColour}`}>{label}</span>
      <h1 className="text-[28px] leading-[1.15]">{title}</h1>
      {children}
    </div>
  );
}

export function Reference({ value }: { value: string }) {
  return (
    <div className="flex justify-between border-t border-line pt-3 text-[13px] text-ink-2">
      <span>Reference</span>
      <span className="font-mono text-ink">{value}</span>
    </div>
  );
}

function LockIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="11" width="14" height="10" rx="1.5" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

function CheckCircle() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="m8 12.5 2.6 2.5L16 9.5" />
    </svg>
  );
}

function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    check: <path d="m5.5 12.5 4.2 4.2L18.5 8" />,
    x: (
      <>
        <path d="M7 7l10 10" />
        <path d="M17 7 7 17" />
      </>
    ),
    back: (
      <>
        <path d="M9 14 4 9l5-5" />
        <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="13" r="8" />
        <path d="M12 9v4l2.5 2" />
      </>
    ),
    warn: (
      <>
        <path d="M12 3 2.5 20h19L12 3Z" />
        <path d="M12 10v4.5" />
        <path d="M12 17.5v.01" />
      </>
    ),
    progress: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
  };
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  );
}

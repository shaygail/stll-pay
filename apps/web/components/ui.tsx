import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

/** Shared building blocks that follow docs/DESIGN_SYSTEM.md. */

type Variant = "primary" | "secondary" | "text";

const base =
  "inline-flex items-center justify-center gap-2 rounded-md px-5 text-[15px] font-medium transition-opacity disabled:opacity-50 disabled:cursor-not-allowed";
const variants: Record<Variant, string> = {
  primary: "h-[52px] bg-ink text-on-ink hover:opacity-90",
  secondary: "h-[52px] border border-line-strong text-ink hover:bg-surface",
  text: "h-11 text-ink underline-offset-4 hover:underline",
};

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ComponentProps<"button"> & { variant?: Variant }) {
  return <button className={`${base} ${variants[variant]} ${className}`} {...props} />;
}

export function ButtonLink({
  variant = "primary",
  className = "",
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link className={`tap ${base} ${variants[variant]} ${className}`} {...props} />;
}

export function Field({
  label,
  hint,
  error,
  id,
  ...input
}: ComponentProps<"input"> & { label: string; hint?: string; error?: string; id: string }) {
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ");
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="eyebrow">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className="h-12 rounded-md border border-line-strong bg-bg px-3.5 text-ink placeholder:text-ink-2"
        {...input}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-[13px] text-ink-2">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-[13px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function Notice({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "error" | "strong";
  children: ReactNode;
}) {
  const styles = {
    neutral: "bg-surface text-ink",
    error: "border border-danger text-danger",
    strong: "bg-ink text-on-ink",
  }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`rounded-md p-3.5 text-[14px] ${styles}`}>
      {children}
    </div>
  );
}

export function Wordmark({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="wordmark text-ink no-underline">
      STLL PAY
    </Link>
  );
}

export function AuthShell({ title, eyebrow, children }: { title: string; eyebrow?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 items-center border-b border-line px-5">
        <Wordmark />
      </header>
      <main className="mx-auto flex w-full max-w-[440px] flex-1 flex-col gap-6 px-5 py-8">
        <div className="flex flex-col gap-1">
          {eyebrow && <span className="eyebrow">{eyebrow}</span>}
          <h1 className="text-[26px] leading-tight">{title}</h1>
        </div>
        {children}
      </main>
    </div>
  );
}

export function StatusChip({ status }: { status: string }) {
  const label: Record<string, string> = {
    draft: "Draft",
    pending_review: "Pending review",
    restricted: "Restricted",
    active: "Active",
    suspended: "Suspended",
  };
  const tone = status === "active" ? "border-sage-ink text-sage-ink" : status === "suspended" ? "border-danger text-danger" : "border-line-strong text-ink-2";
  return (
    <span className={`whitespace-nowrap rounded-sm border px-2 py-0.5 text-[12px] ${tone}`}>
      {label[status] ?? status}
    </span>
  );
}

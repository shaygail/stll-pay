import { ButtonLink, Wordmark } from "@/components/ui";

export default function Home() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 items-center justify-between border-b border-line px-5">
        <Wordmark />
        <nav aria-label="Account" className="flex items-center gap-1">
          <ButtonLink href="/login" variant="text">
            Log in
          </ButtonLink>
        </nav>
      </header>
      <main className="mx-auto flex w-full max-w-[720px] flex-1 flex-col justify-center gap-8 px-5 py-16">
        <div className="flex flex-col gap-4">
          <span className="eyebrow">From STLL HAUS</span>
          <h1 className="text-[clamp(2rem,7vw,3rem)] leading-[1.05]">
            Bank payments by QR code, for New Zealand businesses.
          </h1>
          <p className="max-w-[52ch] text-[17px] text-ink-2">
            Customers scan, approve the payment in their own bank, and you see it confirmed. No card
            terminal, and no one ever shares their banking login with us.
          </p>
        </div>
        <ol className="grid gap-3 text-[15px] sm:grid-cols-3">
          {["Customer scans your QR", "They approve in their bank", "You see it confirmed"].map((step, i) => (
            <li key={step} className="flex gap-3 rounded-md border border-line p-4">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm border border-line-strong text-[12px]">
                {i + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/signup">Create a business account</ButtonLink>
          <ButtonLink href="/login" variant="secondary">
            Log in
          </ButtonLink>
        </div>
        <p className="text-[13px] text-ink-2">
          STLL PAY is in development. It isn&apos;t taking real payments yet.
        </p>
      </main>
    </div>
  );
}

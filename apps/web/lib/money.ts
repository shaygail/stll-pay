/**
 * Money helpers. Amounts are always integer NZD cents; floats never hold money.
 * The server re-validates every amount, so these are for display and input only.
 */

const MAX_CENTS = 100_000_000; // $1,000,000.00, a UI ceiling; real limits come from banks

export function formatNZD(cents: number): string {
  if (!Number.isSafeInteger(cents)) throw new TypeError("cents must be an integer");
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const dollars = Math.trunc(abs / 100).toLocaleString("en-NZ");
  const rest = String(abs % 100).padStart(2, "0");
  return `${sign}$${dollars}.${rest}`;
}

/**
 * Parse what a customer typed ("12", "12.5", "$1,234.50") into cents with string
 * arithmetic, so float rounding errors can't creep in. Returns null if invalid.
 */
export function parseNZDToCents(input: string): number | null {
  const cleaned = input.trim().replace(/^\$/, "").replace(/,/g, "");
  const match = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const cents = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
  if (cents <= 0 || cents > MAX_CENTS) return null;
  return cents;
}

import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

export const OUTBOX = path.resolve(__dirname, ".outbox");
export const PASSWORD = "Correct-Horse-42-battery";

export function uniqueEmail(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

/** The newest email the API wrote to the file outbox for this address. */
export function latestEmailTo(address: string): { subject: string; body: string } {
  const files = readdirSync(OUTBOX).filter((f) => f.endsWith(".json")).sort().reverse();
  for (const f of files) {
    const msg = JSON.parse(readFileSync(path.join(OUTBOX, f), "utf8"));
    if (msg.to === address) return msg;
  }
  throw new Error(`no email to ${address}`);
}

function base32Decode(input: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of input.replace(/=+$/, "").toUpperCase()) {
    bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s), the same as authenticator apps. */
export function totp(secret: string, offsetSteps = 0): string {
  const step = Math.floor(Date.now() / 1000 / 30) + offsetSteps;
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const hmac = createHmac("sha1", base32Decode(secret)).update(counter).digest();
  const offset = hmac[hmac.length - 1] & 0xf;
  const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(code).padStart(6, "0");
}

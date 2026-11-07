"use client";

import { useCallback, useEffect, useState } from "react";
import { renderSVG } from "uqr";
import { Button, Field, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

type Qr = { id: string; label: string; code: string; active: boolean; url: string };

/** Counter QR codes: print one per till or stall. Customers type the amount. */
export function QrCodesPanel({ merchantId, canManage }: { merchantId: string; canManage: boolean }) {
  const [items, setItems] = useState<Qr[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    api<{ items: Qr[] }>(`/v1/merchants/${merchantId}/qr-codes`).then(
      (r) => alive && setItems(r.items),
      (err) => alive && setError(err instanceof ApiError ? err.message : "Couldn't load QR codes."),
    );
    return () => {
      alive = false;
    };
  }, [merchantId, version]);

  async function onCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const el = e.currentTarget;
    try {
      const qr = await api<Qr>(`/v1/merchants/${merchantId}/qr-codes`, { body: { label: form.get("label") } });
      el.reset();
      setSelected(qr.id);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the QR code.");
    }
  }

  async function toggle(qr: Qr) {
    try {
      await api(`/v1/merchants/${merchantId}/qr-codes/${qr.id}`, { method: "PATCH", body: { active: !qr.active } });
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update the QR code.");
    }
  }

  const shown = items?.find((q) => q.id === selected) ?? items?.find((q) => q.active) ?? null;

  return (
    <section aria-labelledby="qr-codes" className="flex flex-col gap-4 rounded-md border border-line p-5">
      <div className="flex flex-col gap-1">
        <h2 id="qr-codes" className="text-[17px]">Counter QR codes</h2>
        <p className="text-[14px] text-ink-2">Print and display these. Customers scan, type the amount, and pay.</p>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="grid gap-5 md:grid-cols-2">
        <div className="flex flex-col gap-3">
          {items && items.length === 0 && <p className="text-[14px] text-ink-2">No QR codes yet.</p>}
          <ul className="flex flex-col border-t border-line">
            {items?.map((q) => (
              <li key={q.id} className="flex min-h-[52px] items-center justify-between gap-3 border-b border-line py-2">
                <button
                  onClick={() => setSelected(q.id)}
                  className={`min-h-0 text-left text-[15px] ${q.active ? "" : "text-ink-2 line-through"}`}
                  aria-pressed={shown?.id === q.id}
                >
                  {q.label}
                </button>
                {canManage && (
                  <button onClick={() => toggle(q)} className="h-9 min-h-0 rounded-md border border-line-strong px-3 text-[13px]">
                    {q.active ? "Turn off" : "Turn on"}
                  </button>
                )}
              </li>
            ))}
          </ul>
          {canManage && (
            <form onSubmit={onCreate} className="flex items-end gap-2" aria-label="New QR code" noValidate>
              <div className="flex-1">
                <Field id="qr-label" name="label" label="Label" placeholder="e.g. Front counter" maxLength={80} required />
              </div>
              <Button type="submit" variant="secondary">Add</Button>
            </form>
          )}
        </div>
        {shown && (
          <figure className="flex flex-col items-center gap-2">
            <div
              role="img"
              aria-label={`QR code: ${shown.label}`}
              className="w-[220px] rounded-md bg-white p-3"
              dangerouslySetInnerHTML={{ __html: renderSVG(shown.url) }}
            />
            <figcaption className="text-center text-[13px] text-ink-2">
              {shown.label}
              {!shown.active && " (turned off)"}
            </figcaption>
            <Button variant="text" onClick={() => window.print()}>Print</Button>
          </figure>
        )}
      </div>
    </section>
  );
}

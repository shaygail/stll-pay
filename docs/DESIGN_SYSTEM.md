# STLL PAY design system

**Status:** Phase 2 draft, awaiting approval.
**Source of truth:** STLL REWARDS (`shaygail/stll-haus-app`, `app/globals.css`), copied
exactly so STLL PAY looks like part of app.stllhaus.co.
**Screens:** the Phase 2 design canvas (14 artboards). A copy of its source is in
`design/canvas/`.

## 1. Tokens

Copied from STLL REWARDS. Do not change them in STLL PAY alone; change them in both apps.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f5f3ef` | `#121211` | Page |
| `--surface` | `#ecebe6` | `#1b1b19` | Quiet panels, notes |
| `--surface-2` | `#e3e1db` | `#252522` | Selected nav, pressed |
| `--ink` | `#1b1b19` | `#f2f0eb` | Text, primary buttons |
| `--ink-2` | `#55534e` | `#b4b1aa` | Secondary text, **all small labels** |
| `--muted` | `#8f8c85` | `#7d7a74` | Decorative only (see §3) |
| `--line` / `--line-strong` | ink at 10% / 22% | | Hairlines and input borders |
| `--sage` | `#7a8b78` | `#8fa18c` | Marks and fills only (chart bars, paid tint) |
| `--sage-ink` | `#4f5e4d` | `#b5c4b2` | "Paid" and "verified" **text** |
| `--sage-soft` | sage at 16% | | Paid icon tile |
| `--danger` | `#a3473f` | `#e08b82` | "Not paid", declined |

| Rule | Value |
|---|---|
| Radius | 4px for controls and cards, 2px for tags and status chips |
| Shadows | None |
| Lines | 1px hairlines; 1.5px ink for focus and for anything that needs action |
| Spacing | 4px scale. Page gutter 20px on phone, 32px on desktop |
| Type | Geist 400 and 500 (600 only for one bolded phrase); Geist Mono for payment references |
| Headings | Weight 500, letter-spacing −0.02em |
| Eyebrow labels | 11px, weight 500, letter-spacing 0.12em, uppercase, `--ink-2` |
| Wordmark | "STLL PAY", 11–12px, weight 500, letter-spacing 0.32em |
| Amounts | Weight 500, letter-spacing −0.03em, `tabular-nums`. Hero 44–56px |
| Touch | 44px minimum targets; 52px for the main action; 16px inputs (stops iOS zoom) |
| Motion | Under 200ms, fades only; respects `prefers-reduced-motion` |
| Theme | Light, Dark or System, applied before first paint, as in REWARDS |

## 2. Payment status language

Every status pairs **a text label** with a tone, and an icon where space allows. Colour is
never the only signal.

| Internal status | Customer sees | Merchant and POS see | Tone |
|---|---|---|---|
| `CREATED`, `AWAITING_AUTHORISATION` | "Waiting for you to approve" | Waiting | ink-2 |
| `AUTHORISED`, `PROCESSING` | "Your bank is processing…" with **Processing · not paid yet** | Processing, **don't hand over yet** | ink-2 |
| `SENT` | **Paid · confirmed by your bank** | Paid | sage-ink |
| `FAILED` | "Your payment didn't go through" | Declined / Failed | danger |
| `CANCELLED` | "You cancelled the payment" | Cancelled | ink-2 |
| `EXPIRED` | "This payment request has expired" | Expired | ink-2 |
| `REQUIRES_REVIEW` | "We can't confirm this payment yet", **Please don't pay again** | Needs review (outlined in ink) | ink |

**Writing rules:**
- Say "Paid" only for `SENT`.
- The return-from-bank page never says paid by itself.
- Never say "error" to a customer. Say what happened, whether money moved, and what to do next.
- Banks don't share decline reasons, so don't guess one. Suggest common causes only as "a common one is…".
- Always show the reference on failure, cancel and review screens, so staff can find the payment.

## 3. Accessibility (WCAG 2.2 AA)

Contrast was measured on `--bg` `#f5f3ef`:

| Pair | Ratio | Verdict |
|---|---|---|
| `--ink-2` text | 6.93:1 | ✅ any size |
| `--sage-ink` text | 6.23:1 | ✅ any size |
| `--danger` text | 5.37:1 | ✅ any size |
| `--sage` (as text) | 3.27:1 | ❌ for text. ✅ for non-text marks (3:1) |
| `--muted` (as text) | 3.03:1 | ❌ for text. **Decorative or disabled only** |

**This is a difference from STLL REWARDS:** REWARDS uses `--muted` for some small labels.
STLL PAY uses `--ink-2` for every small label.

Other rules:
- Real `<button>`, `<a>` and `<label>`/`<input>` elements throughout.
- Focus is a 1.5px ink outline with 2px offset.
- Status updates are announced with `role="status"`.
- The QR always has a text alternative and a "Show link" fallback.
- Charts have a "View as table" link and a summary `aria-label`.

## 4. Components (built in Phase 3)

| Component | Notes |
|---|---|
| `AppHeader` | 56px; wordmark left; context right (secure note, timer, menu) |
| `MerchantIdentity` | Logo tile, name, "Verified business" (sage-ink + tick) |
| `AmountInput` | 48px tabular figures, underline field, `inputmode="decimal"`; validated as cents on the server |
| `AmountDisplay` | Hero amount with eyebrow label |
| `OrderLines` | Hairline-separated rows plus a mono reference |
| `StepList` | Numbered or checked steps, matching REWARDS `VerticalStepper` |
| `StatusChip` | 2px radius, outlined, text label (see §2) |
| `StatusScreen` | Icon tile, eyebrow, heading, body, details, actions. Covers all six end states |
| `Notice` | `--surface` panel with icon; the inverted ink version is only for "don't pay again" |
| `PrimaryButton` / `SecondaryButton` / `TextButton` | 52 / 52 / 44px |
| `KpiTile` | Label, value, honest sub-line (for example "confirmed by bank") |
| `BarTrend` | Single series, sage bars, 4px top radius, 2px gaps, hover tooltip, table view |
| `DataTable` | Hairlines, scrolls in its own box on a phone |
| `SideNav` | Stacks above content on a phone |
| `TestModeBanner` | Ink bar across the top whenever the provider is mock or sandbox |
| `QrPanel` | White quiet zone, amount, live status, expiry, cancel |

## 5. Layouts

| Surface | Size | Notes |
|---|---|---|
| Customer checkout | 390px phone first; centred 440px column on desktop | One task per screen; main action pinned at the bottom |
| POS (iPad) | 1180×820 landscape | Order on the left, QR on the right. STLL PAY is one more payment button next to Cash and EFTPOS |
| Merchant dashboard | Fluid; sidebar becomes a menu under 768px | Received (confirmed) is always shown apart from Processing |
| Platform admin | Fluid, **dark theme by default** | So no one confuses the admin workspace with a merchant account |

## 6. Decisions in this draft

1. The Akahu explanation and the Akahu logo appear **before** the redirect, as Akahu
   accreditation requires a consumer information page. The real logo comes from Akahu's
   media kit; the canvas shows a labelled placeholder.
2. The Review screen says up front that we show "Paid" only once the bank confirms.
3. The **Processing** screen tells the customer not to pay again, and that it's safe to
   close the page.
4. The **Unconfirmed** screen is the only inverted (ink) panel in checkout, kept for the
   one case where paying twice is a real risk.
5. **Expired POS QRs** offer no retry, because staff must issue a new request. Counter QR
   and payment links can start again.
6. Satoshi is deferred, as in REWARDS. All text is Geist.

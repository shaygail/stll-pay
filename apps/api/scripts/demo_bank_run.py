"""Run the Akahu Demo Bank checks and write a report.

Usage (sandbox credentials only; refuses production and live mode):

    DEMO_BANK_PAYEE_ACCOUNT=12-3456-7890123-00 DEMO_BANK_PAYEE_NAME="STLL HAUS" \\
      uv run python scripts/demo_bank_run.py            # automatic checks only
    ... uv run python scripts/demo_bank_run.py --interactive   # plus approval scenarios

Demo Bank needs a *real* NZ account number as the payee (its own accounts are refused).
No money moves. Automatic checks need no human. Interactive scenarios print an Akahu link
per payment: open it, pick Demo Bank, log in with any username and password, answer the
maths question, and approve. The script then polls Akahu and compares the outcome with the
Demo Bank documentation (https://developers.akahu.nz/docs/demo-bank).

Webhooks aren't tested here (Akahu can't reach a local machine); polling is used, and the
webhook code path is covered by tests against a documented fake.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.ids import random_code  # noqa: E402
from app.payments.providers.akahu import AkahuPaymentProvider  # noqa: E402
from app.payments.providers.base import CreatePayment, ProviderError  # noqa: E402
from app.payments.states import FINAL, Status  # noqa: E402

# Documented one-off outcomes (Demo Bank page, "Magic amounts").
SCENARIOS: list[tuple[int, str, Status, str]] = [
    (1000, "normal amount: settles", Status.SENT, "SENT"),
    (502, "internal error", Status.FAILED, "FAILED/INTERNAL_ERROR"),
    (503, "bank error", Status.FAILED, "FAILED/BANK_ERROR"),
    (504, "indeterminate bank error", Status.REQUIRES_REVIEW, "FAILED/BANK_ERROR_INDETERMINATE"),
    (515, "insufficient funds (reported as REJECTED)", Status.FAILED, "FAILED/REJECTED"),
    (520, "30 s delay then settles", Status.SENT, "SENT"),
    (522, "30 s delay then fails", Status.FAILED, "FAILED/REJECTED"),
    (10515, "105.15 alias of 5.15", Status.FAILED, "FAILED/REJECTED"),
]


@dataclass
class Result:
    name: str
    expected: str
    actual: str
    ok: bool
    note: str = ""


def _request(amount: int, *, minutes: int = 20) -> CreatePayment:
    settings = get_settings()
    ref = "DB" + random_code(8)
    return CreatePayment(
        amount_cents=amount,
        payee_name=os.environ["DEMO_BANK_PAYEE_NAME"],
        payee_account=os.environ["DEMO_BANK_PAYEE_ACCOUNT"],
        reference=ref, payer_reference=ref,
        redirect_uri=f"{settings.web_origin}/demo-bank-done",
        webhook_uri=None,
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        payer_bank="demo_bank",
    )


async def automatic(provider: AkahuPaymentProvider) -> list[Result]:
    results: list[Result] = []

    try:
        banks = [c.get("bank") for c in await provider.connections()]
        results.append(Result("Credentials and connections", "a list of banks",
                              ", ".join(map(str, banks)), bool(banks)))
    except ProviderError as exc:
        results.append(Result("Credentials and connections", "a list of banks",
                              f"{exc.code}: {exc}", False))
        return results  # nothing else will work

    try:
        await provider.create_payment(_request(1000, minutes=5))
        results.append(Result("Expiry under 15 minutes", "refused (400)", "accepted", False,
                              "Docs say expires_at must be 15 min to 1 year ahead"))
    except ProviderError as exc:
        results.append(Result("Expiry under 15 minutes", "refused (400)", exc.code, True))

    try:
        await provider.create_payment(_request(501))
        results.append(Result("5.01 create fails", "500 error", "created", False))
    except ProviderError as exc:
        results.append(Result("5.01 create fails", "500 error", f"{exc.kind}/{exc.code}",
                              exc.code == "provider_error"))

    created = await provider.create_payment(_request(1000))
    got = await provider.get_payment(created.provider_payment_id)
    results.append(Result("Create then get", "READY, same amount and payee",
                          f"{got.status}, {got.amount_cents}c, {got.payee_account}",
                          got.status == "READY" and got.amount_cents == 1000
                          and got.payee_account == os.environ["DEMO_BANK_PAYEE_ACCOUNT"]))
    await provider.cancel_payment(created.provider_payment_id)
    got = await provider.get_payment(created.provider_payment_id)
    results.append(Result("Cancel while READY", "CANCELLED/APP_CANCELLED",
                          f"{got.status}/{got.status_code}",
                          (got.status, got.status_code) == ("CANCELLED", "APP_CANCELLED")))
    return results


async def interactive(provider: AkahuPaymentProvider, wait_minutes: int) -> list[Result]:
    pending = []
    print("\nOpen each link, choose Demo Bank, log in with anything, answer the maths, approve:")
    for amount, label, target, expected in SCENARIOS:
        created = await provider.create_payment(_request(amount))
        pending.append((amount, label, target, expected, created.provider_payment_id))
        print(f"  ${amount / 100:>7.2f}  {label:<42} {created.authorisation_url}")
    print(f"\nPolling for up to {wait_minutes} minutes...\n")
    deadline = datetime.now(UTC) + timedelta(minutes=wait_minutes)
    done: dict[str, Result] = {}
    while pending and datetime.now(UTC) < deadline:
        for item in list(pending):
            amount, label, target, expected, pid = item
            p = await provider.get_payment(pid)
            mapped = provider.normalise(p.status, p.status_code)
            if mapped in FINAL or mapped in (Status.SENT, Status.REQUIRES_REVIEW):
                actual = p.status + (f"/{p.status_code}" if p.status_code else "")
                done[pid] = Result(f"${amount / 100:.2f} {label}", expected, actual,
                                   mapped == target and actual == expected,
                                   f"maps to {mapped}")
                pending.remove(item)
                print(f"  {done[pid].name}: {actual} -> {mapped}")
        await asyncio.sleep(5)
    for amount, label, _target, expected, _pid in pending:
        done[_pid] = Result(f"${amount / 100:.2f} {label}", expected, "no final status in time",
                            False, "not approved, or still processing")
    return list(done.values())


def write_report(results: list[Result], path: Path) -> None:
    lines = [f"# Akahu Demo Bank run, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", "",
             f"API: {get_settings().akahu_api_url}. Sandbox credentials. No money moved.", "",
             "| Check | Expected (docs) | Actual | Pass | Note |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.name} | {r.expected} | {r.actual} | {'✅' if r.ok else '❌'} |"
                     f" {r.note} |")
    passed = sum(r.ok for r in results)
    lines += ["", f"**{passed} of {len(results)} checks passed.**", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    print("\n".join(lines))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--wait-minutes", type=int, default=15)
    args = parser.parse_args()
    settings = get_settings()
    if settings.environment == "production" or settings.live_payments_enabled:
        raise SystemExit("Refusing to run Demo Bank checks in production or live mode.")
    for var in ("DEMO_BANK_PAYEE_ACCOUNT", "DEMO_BANK_PAYEE_NAME"):
        if not os.environ.get(var):
            raise SystemExit(f"Set {var} (a real NZ account; no money moves).")
    provider = AkahuPaymentProvider(live=False)
    results = await automatic(provider)
    if args.interactive and all(r.ok for r in results[:1]):
        results += await interactive(provider, args.wait_minutes)
    out = ROOT.parents[1] / "docs" / "reports" / f"demo-bank-{datetime.now(UTC):%Y%m%d-%H%M}.md"
    write_report(results, out)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""A fake Akahu one-off payments API, built only from Akahu's documentation.

Used to test AkahuPaymentProvider and its wiring into the payment engine without network
access or credentials. It is not evidence of how the real API behaves; Demo Bank runs
(scripts/demo_bank_run.py) are.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TOKEN, SECRET = "app_token_test", "app_secret_test"


@dataclass
class FakeAkahu:
    payments: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    key_fetches: int = 0
    fail_with: int | None = None  # force an HTTP status on the next call
    outbox: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.key_id = "key_" + secrets.token_hex(4)

    # ------------------------------------------------------------ HTTP handler

    def handler(self, request: httpx.Request) -> httpx.Response:
        expected = "Basic " + base64.b64encode(f"{TOKEN}:{SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"success": False, "message": "Unauthorized",
                                             "issues": []})
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if self.fail_with:
            status, self.fail_with = self.fail_with, None
            return httpx.Response(status, json={"success": False, "message": "forced",
                                                 "issues": [{"code": "forced", "message": "x",
                                                             "path": []}]})
        parts = request.url.path.strip("/").split("/")
        if request.method == "POST" and parts == ["v1", "one-off-payments"]:
            return self._create(body)
        if request.method == "GET" and parts[:2] == ["v1", "one-off-payments"] and len(parts) == 3:
            p = self.payments.get(parts[2])
            return httpx.Response(200, json=self._public(p)) if p else httpx.Response(404, json={
                "success": False, "message": "Not found", "issues": []})
        if request.method == "PUT" and len(parts) == 4 and parts[3] == "cancel":
            p = self.payments.get(parts[2])
            if p is None or p["status"] != "READY":
                return httpx.Response(400, json={"success": False, "message": "Cannot cancel",
                                                 "issues": [{"code": "invalid_status",
                                                             "message": "x", "path": []}]})
            self.set_status(parts[2], "CANCELLED", "APP_CANCELLED")
            return httpx.Response(204)
        if request.method == "GET" and parts[:3] == ["v1", "webhooks", "keys"]:
            if parts[3] != self.key_id:
                return httpx.Response(404, json={"success": False, "message": "No key",
                                                 "issues": []})
            self.key_fetches += 1
            pem = self.key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            return httpx.Response(200, json={"keyId": self.key_id, "publicKey": pem.decode()})
        if request.method == "GET" and parts == ["v1", "connections"]:
            return httpx.Response(200, json={"success": True, "items": [
                {"_id": "conn_demo", "bank": "demo_bank", "name": "Demo Bank", "logo": "x",
                 "limits": {"oneoff": [{"value": 10000, "type": "single_payment",
                                        "classification": "strict", "description": "x"}]}}]})
        return httpx.Response(404)

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        payee = body.get("payee") or {}
        for key in ("particulars", "code", "reference"):
            if len(payee.get(key) or "") > 12:
                return httpx.Response(400, json={"success": False, "message": "Invalid payee",
                                                 "issues": [{"code": "invalid_payee",
                                                             "message": key, "path": []}]})
        pid = "one_off_payment_" + secrets.token_hex(12)
        self.payments[pid] = {"body": body, "status": "READY", "code": None}
        return httpx.Response(200, json={
            "_id": pid, "authorisation_url": f"https://payments.akahu.nz/?payment={pid}",
            "expires_at": body["expires_at"]})

    def _public(self, p: dict[str, Any]) -> dict[str, Any]:
        b = p["body"]
        out = {"_id": next(k for k, v in self.payments.items() if v is p),
               "status": p["status"], "amount": b["amount"], "payee": b["payee"],
               "redirect_uri": b["redirect_uri"], "webhook_uri": b.get("webhook_uri"),
               "created_at": "2026-11-07T00:00:00.000Z", "updated_at": "2026-11-07T00:00:00Z",
               "expires_at": b["expires_at"]}
        if p["code"]:
            out["status_reason"] = {"code": p["code"], "message": "x"}
        return out

    # ------------------------------------------------------------ simulated bank activity

    def set_status(self, pid: str, status: str, code: str | None = None) -> None:
        p = self.payments[pid]
        p["status"], p["code"] = status, code
        uri = p["body"].get("webhook_uri")
        if not uri:
            return
        body = {"type": "ONE_OFF_PAYMENT_STATUS_UPDATE", "_payment": pid, "status": status,
                "emitted_at": datetime.now(UTC).isoformat()}
        if code:
            body["status_reason"] = {"code": code, "message": "x"}
        raw = json.dumps(body).encode()
        sig = base64.b64encode(self.key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()
        self.outbox.append((uri, raw, {"X-Akahu-Payments-Signature": sig,
                                       "X-Akahu-Payments-Signing-Key": self.key_id}))

    def approve_and_send(self, pid: str) -> None:
        self.set_status(pid, "AUTHORISED")
        self.set_status(pid, "SUBMITTED", "PENDING")
        self.set_status(pid, "SENT")

    async def deliver(self, client: httpx.AsyncClient) -> list[int]:
        codes = []
        while self.outbox:
            uri, raw, headers = self.outbox.pop(0)
            codes.append((await client.post(uri, content=raw, headers=headers)).status_code)
        return codes

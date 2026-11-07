"""Merchant roles and permissions (least privilege)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    CASHIER = "cashier"
    VIEWER = "viewer"


class Perm(StrEnum):
    MERCHANT_READ = "merchant:read"
    MERCHANT_UPDATE = "merchant:update"
    MERCHANT_SUBMIT = "merchant:submit"
    TEAM_READ = "team:read"
    TEAM_MANAGE = "team:manage"
    PAYMENTS_READ = "payments:read"
    PAYMENTS_CREATE = "payments:create"
    PAYMENTS_CANCEL = "payments:cancel"
    BANK_MANAGE = "bank:manage"
    INTEGRATIONS_MANAGE = "integrations:manage"
    AUDIT_READ = "audit:read"


_ALL = set(Perm)
ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.OWNER: frozenset(_ALL),
    Role.MANAGER: frozenset(_ALL - {Perm.TEAM_MANAGE, Perm.BANK_MANAGE, Perm.MERCHANT_SUBMIT}),
    Role.CASHIER: frozenset({Perm.MERCHANT_READ, Perm.PAYMENTS_READ, Perm.PAYMENTS_CREATE,
                             Perm.PAYMENTS_CANCEL}),
    Role.VIEWER: frozenset({Perm.MERCHANT_READ, Perm.PAYMENTS_READ}),
}

# Actions that need MFA within the last few minutes, not just at login.
STEP_UP_MINUTES = 10


def has_perm(role: str, perm: Perm) -> bool:
    try:
        return perm in ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False

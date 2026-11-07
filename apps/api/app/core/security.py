"""Password hashing, opaque tokens, field encryption and TOTP."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

# ---------------------------------------------------------------- passwords

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
# A fixed hash used to spend the same time when an email is unknown (no user enumeration).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
_COMMON = {
    "password1234", "123456789012", "qwertyuiop12", "letmein12345", "iloveyou1234",
    "passwordpassword", "aotearoa1234", "newzealand12", "welcome12345", "administrator",
}


def password_problems(password: str, email: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) < 5:
        problems.append("This password is too easy to guess.")
    local = email.split("@", 1)[0].lower()
    if len(local) >= 4 and local in lowered:
        problems.append("Don't include your email address in your password.")
    classes = sum(
        [any(c.islower() for c in password), any(c.isupper() for c in password),
         any(c.isdigit() for c in password), any(not c.isalnum() for c in password)]
    )
    if classes < 3:
        problems.append("Mix at least three of: lower case, upper case, numbers, symbols.")
    return problems


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ---------------------------------------------------------------- opaque tokens


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    """Tokens are stored only as SHA-256 hashes; a database leak doesn't leak sessions."""
    return hashlib.sha256(token.encode()).digest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ---------------------------------------------------------------- field encryption


def _fernet() -> Fernet:
    return Fernet(get_settings().app_encryption_key.get_secret_value())


def encrypt_field(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_field(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - indicates key misconfiguration
        raise RuntimeError("Could not decrypt a stored secret; check APP_ENCRYPTION_KEY") from exc


# ---------------------------------------------------------------- TOTP

TOTP_ISSUER = "STLL PAY"


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def verify_totp(
    secret: str, code: str, last_step: int | None, now: float | None = None
) -> int | None:
    """Check a 6-digit code, allowing one step of clock drift.

    Returns the matched time step, or None. A step at or before ``last_step`` is rejected,
    so a code can't be replayed.
    """
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now = time.time() if now is None else now
    current = int(now // totp.interval)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None

"""PIN hashing, OTP codes and session tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

ALGORITHM = "HS256"
_ACCOUNT_ALPHABET = string.ascii_uppercase + string.digits


def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode(), pin_hash.encode())
    except ValueError:
        return False


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str, secret_key: str) -> str:
    """Keyed hash, so a leaked database doesn't reveal live codes."""
    return hmac.new(secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def otp_matches(code: str, code_hash: str, secret_key: str) -> bool:
    return hmac.compare_digest(hash_otp(code, secret_key), code_hash)


def generate_account_no() -> str:
    return "RS-" + "".join(secrets.choice(_ACCOUNT_ALPHABET) for _ in range(8))


def create_access_token(account_id: int, token_version: int, secret_key: str, minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(account_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, secret_key: str) -> dict:
    """Raises jwt.PyJWTError on a bad, tampered or expired token."""
    return jwt.decode(token, secret_key, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})


def mask_phone(phone: str) -> str:
    return f"******{phone[-4:]}" if len(phone) >= 4 else "******"

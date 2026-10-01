"""Runtime configuration, read once from environment variables."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("resan")

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{BASE_DIR / 'resan.db'}"
    # Render/Heroku hand out postgres:// URLs; SQLAlchemy wants an explicit driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    log.warning("SECRET_KEY not set: using a random key, so sessions end when the server restarts.")
    return secrets.token_urlsafe(48)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    secret_key: str = field(default_factory=_secret_key)

    access_token_minutes: int = int(os.environ.get("ACCESS_TOKEN_MINUTES", 15))
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 30
    pin_max_failures: int = 5
    pin_lockout_minutes: int = 15

    # Limits are in paise (₹1 = 100 paise).
    max_single_deposit_paise: int = 10_00_000 * 100
    daily_debit_limit_paise: int = 2_00_000 * 100
    minor_daily_debit_limit_paise: int = 10_000 * 100

    max_upload_bytes: int = 2 * 1024 * 1024
    uploads_dir: Path = BASE_DIR / "uploads"

    twilio_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    twilio_from: str = os.environ.get("TWILIO_PHONE_NUMBER", "")

    # When SMS isn't configured the OTP is shown on screen, clearly labelled as a demo.
    # Set DEMO_MODE=false to forbid that (OTPs then only go out by SMS).
    demo_mode: bool = field(
        default_factory=lambda: _bool(
            "DEMO_MODE",
            default=not all(
                os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER")
            ),
        )
    )

    allowed_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
    )

    @property
    def sms_configured(self) -> bool:
        return bool(self.twilio_sid and self.twilio_token and self.twilio_from)

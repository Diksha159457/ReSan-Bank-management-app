"""Banking operations.

Every balance change is a single conditional UPDATE inside a database
transaction ("subtract X only if the balance is still >= X"). That makes
overdrafts impossible even when requests race, on both SQLite and Postgres,
without relying on application-level locks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import security
from .config import Settings
from .models import Account, Guardian, OTPChallenge, Transaction


class BankError(Exception):
    """A business-rule failure with an HTTP status and a user-facing message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes even for timezone=True columns.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def rupees(paise: int) -> float:
    return round(paise / 100, 2)


# ── OTP challenges ───────────────────────────────────────────────────────────


@dataclass
class IssuedOTP:
    code: str
    expires_in: int


def issue_otp(db: Session, key: str, settings: Settings) -> IssuedOTP:
    existing = db.get(OTPChallenge, key)
    now = _now()
    if existing and (now - _aware(existing.created_at)).total_seconds() < settings.otp_resend_cooldown_seconds:
        raise BankError(
            429, f"Please wait {settings.otp_resend_cooldown_seconds} seconds before requesting another OTP."
        )
    code = security.generate_otp()
    if existing:
        db.delete(existing)
        db.flush()
    db.add(
        OTPChallenge(
            key=key,
            code_hash=security.hash_otp(code, settings.secret_key),
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
            created_at=now,
            attempts=0,
        )
    )
    db.commit()
    return IssuedOTP(code=code, expires_in=settings.otp_ttl_seconds)


def consume_otp(db: Session, key: str, code: str, settings: Settings) -> None:
    """Verify and delete a challenge. Wrong codes count towards a hard attempt limit."""
    challenge = db.get(OTPChallenge, key)
    if not challenge or _aware(challenge.expires_at) < _now():
        if challenge:
            db.delete(challenge)
            db.commit()
        raise BankError(400, "OTP expired or not requested. Please request a new one.")
    if challenge.attempts >= settings.otp_max_attempts:
        db.delete(challenge)
        db.commit()
        raise BankError(429, "Too many wrong OTP attempts. Please request a new one.")
    if not security.otp_matches(code, challenge.code_hash, settings.secret_key):
        challenge.attempts += 1
        db.commit()
        left = settings.otp_max_attempts - challenge.attempts
        raise BankError(400, f"Incorrect OTP. {left} attempt(s) left.")
    db.delete(challenge)
    db.commit()


# ── Accounts ─────────────────────────────────────────────────────────────────


def create_account(db: Session, req, settings: Settings) -> Account:
    consume_otp(db, f"signup:{req.phone}", req.otp, settings)
    for _ in range(5):
        account = Account(
            account_no=security.generate_account_no(),
            name=req.name,
            age=req.age,
            email=req.email,
            phone=req.phone,
            pin_hash=security.hash_pin(req.pin),
            balance_paise=0,
            account_type="Minor Account" if req.age < 18 else "Savings Account",
        )
        if req.age < 18:
            account.guardian = Guardian(
                name=req.guardian_name,
                age=req.guardian_age,
                relationship_=req.guardian_relation,
                phone=req.guardian_phone,
                email=req.guardian_email,
            )
        db.add(account)
        try:
            db.commit()
            return account
        except IntegrityError:  # astronomically unlikely account-number collision
            db.rollback()
    raise BankError(500, "Could not allocate an account number. Please try again.")


def get_open_account(db: Session, account_no: str) -> Account | None:
    return db.scalar(select(Account).where(Account.account_no == account_no, Account.closed_at.is_(None)))


def check_pin(db: Session, account: Account, pin: str, settings: Settings) -> None:
    """Verify a PIN with lockout after repeated failures."""
    locked_until = _aware(account.locked_until)
    if locked_until and locked_until > _now():
        minutes = max(1, int((locked_until - _now()).total_seconds() // 60) + 1)
        raise BankError(423, f"Account locked after too many wrong PINs. Try again in {minutes} minute(s).")
    if security.verify_pin(pin, account.pin_hash):
        if account.failed_pin_attempts or account.locked_until:
            account.failed_pin_attempts = 0
            account.locked_until = None
            db.commit()
        return
    account.failed_pin_attempts += 1
    if account.failed_pin_attempts >= settings.pin_max_failures:
        account.failed_pin_attempts = 0
        account.locked_until = _now() + timedelta(minutes=settings.pin_lockout_minutes)
        db.commit()
        raise BankError(423, f"Too many wrong PINs. Account locked for {settings.pin_lockout_minutes} minutes.")
    db.commit()
    left = settings.pin_max_failures - account.failed_pin_attempts
    raise BankError(401, f"Incorrect PIN. {left} attempt(s) left before the account is locked.")


# ── Money movement ───────────────────────────────────────────────────────────


def _lock(db: Session, *account_ids: int) -> None:
    """Row-lock accounts (Postgres) in a fixed order so concurrent transfers can't deadlock.

    On SQLite this is a no-op: transactions already start with BEGIN IMMEDIATE.
    """
    if db.get_bind().dialect.name == "sqlite":
        return
    for account_id in sorted(set(account_ids)):
        db.execute(select(Account.id).where(Account.id == account_id).with_for_update())


def _debited_today(db: Session, account_id: int) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount_paise), 0)).where(
            Transaction.account_id == account_id,
            Transaction.type.in_(("Debit", "Transfer Out")),
            Transaction.created_at >= start,
        )
    )
    return int(total or 0)


def _check_daily_limit(db: Session, account: Account, amount: int, settings: Settings) -> None:
    limit = settings.minor_daily_debit_limit_paise if account.is_minor else settings.daily_debit_limit_paise
    used = _debited_today(db, account.id)
    if used + amount > limit:
        raise BankError(
            400,
            f"Daily withdrawal/transfer limit is ₹{rupees(limit):,.2f}. "
            f"You can still move ₹{rupees(max(0, limit - used)):,.2f} today.",
        )


def _credit(db: Session, account_id: int, amount: int) -> None:
    db.execute(update(Account).where(Account.id == account_id).values(balance_paise=Account.balance_paise + amount))


def _debit(db: Session, account_id: int, amount: int) -> bool:
    result = db.execute(
        update(Account)
        .where(Account.id == account_id, Account.balance_paise >= amount)
        .values(balance_paise=Account.balance_paise - amount)
    )
    return result.rowcount == 1


def _balance(db: Session, account_id: int) -> int:
    return int(db.scalar(select(Account.balance_paise).where(Account.id == account_id)))


def _record(db: Session, account_id: int, kind: str, amount: int, ref: str, counterparty: str | None = None) -> None:
    db.add(
        Transaction(
            account_id=account_id,
            type=kind,
            amount_paise=amount,
            balance_after_paise=_balance(db, account_id),
            counterparty=counterparty,
            reference=ref,
        )
    )


def deposit(db: Session, account: Account, amount: int, settings: Settings) -> int:
    if amount > settings.max_single_deposit_paise:
        raise BankError(400, f"Single deposit limit is ₹{rupees(settings.max_single_deposit_paise):,.0f}.")
    _credit(db, account.id, amount)
    _record(db, account.id, "Credit", amount, str(uuid.uuid4()))
    db.commit()
    return _balance(db, account.id)


def withdraw(db: Session, account: Account, amount: int, settings: Settings) -> int:
    _lock(db, account.id)
    _check_daily_limit(db, account, amount, settings)
    if not _debit(db, account.id, amount):
        db.rollback()
        raise BankError(400, f"Insufficient balance. Available: ₹{rupees(_balance(db, account.id)):,.2f}")
    _record(db, account.id, "Debit", amount, str(uuid.uuid4()))
    db.commit()
    return _balance(db, account.id)


def transfer(db: Session, source: Account, to_acc: str, amount: int, settings: Settings) -> tuple[int, Account]:
    if to_acc == source.account_no:
        raise BankError(400, "Cannot transfer to the same account.")
    target = get_open_account(db, to_acc)
    if not target:
        raise BankError(404, "Recipient account not found.")
    _lock(db, source.id, target.id)
    _check_daily_limit(db, source, amount, settings)

    ref = str(uuid.uuid4())
    # Debit and credit commit together or not at all.
    if not _debit(db, source.id, amount):
        db.rollback()
        raise BankError(400, f"Insufficient balance. Available: ₹{rupees(_balance(db, source.id)):,.2f}")
    _credit(db, target.id, amount)
    _record(db, source.id, "Transfer Out", amount, ref, counterparty=target.account_no)
    _record(db, target.id, "Transfer In", amount, ref, counterparty=source.account_no)
    db.commit()
    return _balance(db, source.id), target


def list_transactions(db: Session, account: Account, limit: int = 50, offset: int = 0) -> list[Transaction]:
    return list(
        db.scalars(
            select(Transaction)
            .where(Transaction.account_id == account.id)
            .order_by(Transaction.created_at.desc(), Transaction.id)
            .limit(limit)
            .offset(offset)
        )
    )


def close_account(db: Session, account: Account) -> None:
    db.refresh(account)
    if account.balance_paise > 0:
        raise BankError(
            409,
            f"Your balance is ₹{rupees(account.balance_paise):,.2f}. "
            "Withdraw or transfer it before closing the account.",
        )
    account.closed_at = _now()
    account.token_version += 1
    db.commit()

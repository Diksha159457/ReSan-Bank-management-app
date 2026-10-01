"""Database tables.

Money is stored as integer paise (₹1 = 100 paise), never as floats.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance_paise >= 0", name="balance_non_negative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_no: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    age: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str] = mapped_column(String(10))
    pin_hash: Mapped[str] = mapped_column(String(100))
    balance_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    account_type: Mapped[str] = mapped_column(String(20))  # "Savings Account" | "Minor Account"
    kyc_status: Mapped[str] = mapped_column(String(20), default="not_submitted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failed_pin_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bumped on PIN change / closure: invalidates every token issued before.
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    guardian: Mapped[Guardian | None] = relationship(back_populates="account", uselist=False)

    @property
    def is_minor(self) -> bool:
        return self.account_type == "Minor Account"


class Guardian(Base):
    __tablename__ = "guardians"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    age: Mapped[int] = mapped_column(Integer)
    relationship_: Mapped[str] = mapped_column("relationship", String(40))
    phone: Mapped[str] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)

    account: Mapped[Account] = relationship(back_populates="guardian")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # Credit | Debit | Transfer In | Transfer Out
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    balance_after_paise: Mapped[int] = mapped_column(BigInteger)
    counterparty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Both legs of a transfer share one reference, so the pair can be audited together.
    reference: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "signup:9876543210"
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class KYCDocument(Base):
    __tablename__ = "kyc_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    stored_name: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

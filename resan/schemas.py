"""Request validation. Everything a client sends is checked here first."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, field_validator, model_validator

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,79}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_ACC_RE = re.compile(r"^RS-[A-Z0-9]{8}$")

WEAK_PINS = {"0000", "1234", "4321", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
             "1212", "0123", "1004", "2580", "6969", "1122"}  # fmt: skip


def _digits(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _phone(value: object) -> str:
    digits = _digits(value)
    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    return digits


def _pin(value: object) -> str:
    pin = str(value).strip()
    if not re.fullmatch(r"\d{4}", pin):
        raise ValueError("PIN must be exactly 4 digits.")
    return pin


def _new_pin(value: str) -> str:
    if value in WEAK_PINS:
        raise ValueError("That PIN is too easy to guess. Choose a less common one.")
    return value


def _name(value: object) -> str:
    name = re.sub(r"\s+", " ", str(value)).strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError("Enter a name of 2–80 letters (spaces, dots, hyphens and apostrophes allowed).")
    return name


def _email(value: object) -> str:
    email = str(value).strip().lower()
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    return email


def _acc(value: object) -> str:
    acc = str(value).strip().upper()
    if not _ACC_RE.fullmatch(acc):
        raise ValueError("Account numbers look like RS-XXXXXXXX.")
    return acc


def _amount(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    if value.as_tuple().exponent < -2:
        raise ValueError("Amount can have at most 2 decimal places.")
    return value


Phone = Annotated[str, BeforeValidator(_phone)]
Pin = Annotated[str, BeforeValidator(_pin)]
NewPin = Annotated[str, BeforeValidator(_pin), AfterValidator(_new_pin)]
Name = Annotated[str, BeforeValidator(_name)]
Email = Annotated[str, BeforeValidator(_email)]
AccountNo = Annotated[str, BeforeValidator(_acc)]
# Decimal, not float: "0.1 + 0.2" must be exactly 0.30.
Amount = Annotated[Decimal, Field(max_digits=12, decimal_places=2), AfterValidator(_amount)]
OTP = Annotated[str, Field(pattern=r"^\d{6}$")]


def to_paise(amount: Decimal) -> int:
    return int(amount * 100)


class OTPRequest(BaseModel):
    phone: Phone


class SignupRequest(BaseModel):
    name: Name
    age: int = Field(ge=10, le=120)
    email: Email
    phone: Phone
    pin: NewPin
    otp: OTP
    guardian_name: Name | None = None
    guardian_age: int | None = Field(default=None, ge=18, le=120)
    guardian_relation: str | None = Field(default=None, max_length=40)
    guardian_phone: Phone | None = None
    guardian_email: Email | None = None

    @field_validator("guardian_name", "guardian_relation", "guardian_phone", "guardian_email", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("guardian_age", mode="before")
    @classmethod
    def _zero_age_is_none(cls, v):
        return None if v in (0, "", None) else v

    @model_validator(mode="after")
    def _guardian_for_minors(self):
        if self.age < 18:
            missing = [
                f for f in ("guardian_name", "guardian_age", "guardian_relation", "guardian_phone")
                if getattr(self, f) in (None, "")
            ]  # fmt: skip
            if missing:
                raise ValueError("Guardian name, age, relation and phone are required for minor accounts.")
        return self


class LoginOTPRequest(BaseModel):
    acc_no: AccountNo
    pin: Pin


class LoginVerifyRequest(BaseModel):
    acc_no: AccountNo
    otp: OTP


class AmountRequest(BaseModel):
    amount: Amount


class TransferRequest(BaseModel):
    to_acc: AccountNo
    amount: Amount


class UpdateRequest(BaseModel):
    current_pin: Pin
    new_name: Name | None = None
    new_email: Email | None = None
    new_pin: NewPin | None = None

    @field_validator("new_name", "new_email", "new_pin", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if v is None or (isinstance(v, str) and not v.strip()) else v


class CloseRequest(BaseModel):
    pin: Pin
    confirm: str

    @field_validator("confirm")
    @classmethod
    def _must_type_delete(cls, v: str) -> str:
        if v.strip() != "DELETE":
            raise ValueError("Type DELETE to confirm.")
        return v

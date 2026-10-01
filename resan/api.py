"""HTTP routes."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import security, services
from .models import Account, KYCDocument
from .schemas import (
    AmountRequest,
    CloseRequest,
    LoginOTPRequest,
    LoginVerifyRequest,
    OTPRequest,
    SignupRequest,
    TransferRequest,
    UpdateRequest,
    to_paise,
)
from .services import BankError, rupees

log = logging.getLogger("resan.api")
router = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)


# ── Dependencies ─────────────────────────────────────────────────────────────


def get_db(request: Request):
    yield from request.app.state.db.session()


def get_settings(request: Request):
    return request.app.state.settings


DB = Annotated[Session, Depends(get_db)]


def limit(request: Request, bucket: str, key: str, max_hits: int, window: int) -> None:
    client = request.client.host if request.client else "unknown"
    limiter = request.app.state.limiter
    if not (
        limiter.hit(f"{bucket}:ip:{client}", max_hits * 4, window) and limiter.hit(f"{bucket}:{key}", max_hits, window)
    ):
        raise HTTPException(429, "Too many requests. Please slow down and try again shortly.")


def current_account(
    request: Request,
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Account:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "Please sign in.", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = security.decode_access_token(creds.credentials, request.app.state.settings.secret_key)
        account = db.get(Account, int(claims["sub"]))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(
            401, "Session expired. Please sign in again.", headers={"WWW-Authenticate": "Bearer"}
        ) from None
    if account is None or account.closed_at is not None or claims.get("ver") != account.token_version:
        raise HTTPException(401, "Session expired. Please sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return account


Me = Annotated[Account, Depends(current_account)]


def _otp_response(message: str, issued: services.IssuedOTP, sent: bool, settings) -> dict:
    body = {"message": message, "expires_in": issued.expires_in, "delivery": "sms" if sent else "demo"}
    if not sent:
        if not settings.demo_mode:
            raise HTTPException(503, "SMS delivery is unavailable right now. Please try again later.")
        body["dev_otp"] = issued.code  # demo deployments only; labelled as such in the UI
    return body


def _account_view(account: Account) -> dict:
    return {
        "accountNo": account.account_no,
        "name": account.name,
        "age": account.age,
        "email": account.email,
        "phone": security.mask_phone(account.phone),
        "balance": rupees(account.balance_paise),
        "balance_paise": account.balance_paise,
        "accountType": account.account_type,
        "kycStatus": account.kyc_status,
        "createdAt": account.created_at.isoformat(),
        "guardian": (
            {"name": account.guardian.name, "relationship": account.guardian.relationship_}
            if account.guardian
            else None
        ),
    }


def _tx_view(tx) -> dict:
    return {
        "id": tx.id,
        "type": tx.type,
        "amount": rupees(tx.amount_paise),
        "balance": rupees(tx.balance_after_paise),
        "counterparty": tx.counterparty,
        "reference": tx.reference,
        "date": tx.created_at.isoformat(),
    }


# ── Public ───────────────────────────────────────────────────────────────────


@router.get("/health")
def health(request: Request) -> dict:
    s = request.app.state.settings
    return {"status": "ok", "version": request.app.version, "demo_mode": s.demo_mode, "sms": s.sms_configured}


@router.get("/stats")
def stats(db: DB) -> dict:
    open_accounts = select(Account).where(Account.closed_at.is_(None)).subquery()
    total, balance, minors = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(open_accounts.c.balance_paise), 0),
            func.count().filter(open_accounts.c.account_type == "Minor Account"),
        ).select_from(open_accounts)
    ).one()
    return {
        "total_accounts": total,
        "total_balance": rupees(int(balance)),
        "savings_accounts": total - int(minors),
        "minor_accounts": int(minors),
    }


@router.post("/otp/send")
def send_signup_otp(req: OTPRequest, request: Request, db: DB) -> dict:
    settings = request.app.state.settings
    limit(request, "otp", req.phone, max_hits=5, window=3600)
    issued = services.issue_otp(db, f"signup:{req.phone}", settings)
    sent = request.app.state.sms.send(
        req.phone, f"Your ReSan Bank OTP is {issued.code}. Valid for 5 minutes. Never share it."
    )
    return _otp_response("OTP sent to your phone." if sent else "OTP generated.", issued, sent, settings)


@router.post("/account/create", status_code=201)
def create_account(req: SignupRequest, request: Request, db: DB) -> dict:
    limit(request, "signup", req.phone, max_hits=10, window=3600)
    account = services.create_account(db, req, request.app.state.settings)
    return {"message": "Account created successfully!", "accountNo": account.account_no, "name": account.name}


@router.post("/login/otp")
def login_start(req: LoginOTPRequest, request: Request, db: DB) -> dict:
    settings = request.app.state.settings
    limit(request, "login", req.acc_no, max_hits=10, window=900)
    account = services.get_open_account(db, req.acc_no)
    if account is None:
        # Same message and similar cost as a wrong PIN, so account numbers can't be probed.
        security.verify_pin(req.pin, request.app.state.dummy_hash)
        raise HTTPException(401, "Incorrect account number or PIN.")
    services.check_pin(db, account, req.pin, settings)
    issued = services.issue_otp(db, f"login:{account.id}", settings)
    sent = request.app.state.sms.send(account.phone, f"Your ReSan Bank login OTP is {issued.code}. Never share it.")
    return _otp_response(f"OTP sent to {security.mask_phone(account.phone)}.", issued, sent, settings)


@router.post("/login/verify")
def login_verify(req: LoginVerifyRequest, request: Request, db: DB) -> dict:
    settings = request.app.state.settings
    limit(request, "login-verify", req.acc_no, max_hits=10, window=900)
    account = services.get_open_account(db, req.acc_no)
    if account is None:
        raise HTTPException(400, "OTP expired or not requested. Please request a new one.")
    services.consume_otp(db, f"login:{account.id}", req.otp, settings)
    token = security.create_access_token(
        account.id, account.token_version, settings.secret_key, settings.access_token_minutes
    )
    return {
        "message": "Login successful!",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "account": _account_view(account),
    }


# ── Authenticated ────────────────────────────────────────────────────────────


@router.get("/me")
def me(account: Me) -> dict:
    return _account_view(account)


@router.post("/deposit")
def deposit(req: AmountRequest, account: Me, request: Request, db: DB) -> dict:
    amount = to_paise(req.amount)
    balance = services.deposit(db, account, amount, request.app.state.settings)
    return {"message": f"₹{rupees(amount):,.2f} deposited successfully!", "new_balance": rupees(balance)}


@router.post("/withdraw")
def withdraw(req: AmountRequest, account: Me, request: Request, db: DB) -> dict:
    amount = to_paise(req.amount)
    balance = services.withdraw(db, account, amount, request.app.state.settings)
    return {"message": f"₹{rupees(amount):,.2f} withdrawn successfully!", "new_balance": rupees(balance)}


@router.post("/transfer")
def transfer(req: TransferRequest, account: Me, request: Request, db: DB) -> dict:
    limit(request, "transfer", account.account_no, max_hits=30, window=3600)
    amount = to_paise(req.amount)
    balance, target = services.transfer(db, account, req.to_acc, amount, request.app.state.settings)
    return {
        "message": f"₹{rupees(amount):,.2f} transferred to {target.name} ({target.account_no}).",
        "new_balance": rupees(balance),
    }


@router.get("/transactions")
def transactions(
    account: Me,
    db: DB,
    limit_: Annotated[int, Query(alias="limit", ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    db.refresh(account)
    txs = services.list_transactions(db, account, limit_, offset)
    return {"transactions": [_tx_view(t) for t in txs], "balance": rupees(account.balance_paise)}


@router.put("/account/update")
def update_account(req: UpdateRequest, account: Me, request: Request, db: DB) -> dict:
    settings = request.app.state.settings
    if not (req.new_name or req.new_email or req.new_pin):
        raise HTTPException(400, "Enter at least one field to update.")
    services.check_pin(db, account, req.current_pin, settings)  # step-up: re-confirm PIN
    if req.new_name:
        account.name = req.new_name
    if req.new_email:
        account.email = req.new_email
    response = {"message": "Account updated successfully!"}
    if req.new_pin:
        account.pin_hash = security.hash_pin(req.new_pin)
        account.token_version += 1  # sign out every existing session
        response["message"] = "Account updated. Your PIN changed, so please sign in again."
        response["reauth"] = True
    db.commit()
    return response


@router.delete("/account/close")
def close_account(req: CloseRequest, account: Me, request: Request, db: DB) -> dict:
    services.check_pin(db, account, req.pin, request.app.state.settings)
    services.close_account(db, account)
    return {"message": f"Account {account.account_no} closed."}


KYC_KINDS = ("aadhaar", "pan", "address_proof", "guardian_doc")


@router.post("/docs/upload")
async def upload_docs(
    account: Me,
    request: Request,
    db: DB,
    aadhaar: UploadFile = File(...),
    pan: UploadFile = File(...),
    address_proof: UploadFile = File(...),
    guardian_doc: UploadFile | None = File(None),
) -> dict:
    """Demo KYC: files are type- and size-checked and queued for review, never 'auto-verified'."""
    settings = request.app.state.settings
    files = {"aadhaar": aadhaar, "pan": pan, "address_proof": address_proof}
    if guardian_doc is not None and guardian_doc.filename:
        files["guardian_doc"] = guardian_doc

    payloads: dict[str, bytes] = {}
    for kind, upload in files.items():
        data = await upload.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"{kind.replace('_', ' ').title()} exceeds the 2 MB limit.")
        if not data.startswith(b"%PDF-"):  # check the bytes, not the client-supplied content type
            raise HTTPException(415, f"{kind.replace('_', ' ').title()} must be a PDF file.")
        payloads[kind] = data

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    for kind, data in payloads.items():
        stored = f"{secrets.token_hex(16)}.pdf"  # never use the client's filename on disk
        (settings.uploads_dir / stored).write_bytes(data)
        db.add(KYCDocument(account_id=account.id, kind=kind, stored_name=stored, size_bytes=len(data)))
    account.kyc_status = "pending_review"
    db.commit()
    return {"message": "Documents received and queued for review.", "kycStatus": account.kyc_status}


def register_error_handlers(app) -> None:
    from fastapi.responses import JSONResponse

    @app.exception_handler(BankError)
    async def _bank_error(_, exc: BankError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

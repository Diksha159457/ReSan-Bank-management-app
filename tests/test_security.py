"""Attacker's-eye tests: each one is a way the v2 app could be abused."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select

from resan.models import Account, OTPChallenge


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/deposit", {"amount": 10}),
        ("post", "/api/withdraw", {"amount": 10}),
        ("post", "/api/transfer", {"to_acc": "RS-AAAAAAAA", "amount": 10}),
        ("get", "/api/me", None),
        ("get", "/api/transactions", None),
        ("put", "/api/account/update", {"current_pin": "7391", "new_name": "X Y"}),
        ("delete", "/api/account/close", {"pin": "7391", "confirm": "DELETE"}),
    ],
)
def test_money_endpoints_require_a_session(client, method, path, body):
    # v2 accepted account number + PIN alone, skipping the OTP entirely.
    kwargs = {"json": body} if body is not None else {}
    r = client.request(method.upper(), path, **kwargs)
    assert r.status_code == 401


def test_pin_alone_no_longer_moves_money(bank):
    acc, _ = bank.funded("500")
    r = bank.c.post("/api/withdraw", json={"acc_no": acc, "pin": "7391", "amount": 500})
    assert r.status_code == 401


def test_pin_brute_force_locks_the_account(bank):
    acc = bank.open_account(pin="7391")
    codes = [bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": f"{p:04d}"}).status_code
             for p in range(1000, 1006)]  # fmt: skip
    assert codes[:4] == [401] * 4
    assert codes[4] == 423  # 5th failure locks
    # Even the right PIN is refused while locked.
    r = bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "7391"})
    assert r.status_code == 423


def test_lockout_expires(bank, app):
    acc = bank.open_account()
    for p in range(1000, 1005):
        bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": f"{p:04d}"})
    with app.state.db.SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.account_no == acc))
        account.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "7391"}).status_code == 200


def test_unknown_account_and_wrong_pin_look_identical(bank):
    acc = bank.open_account()
    a = bank.c.post("/api/login/otp", json={"acc_no": "RS-ZZZZZZZZ", "pin": "7391"})
    b = bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "1000"})
    assert a.status_code == 401 and b.status_code == 401
    assert "Incorrect" in a.json()["detail"] and "Incorrect" in b.json()["detail"]


def test_otp_is_single_use(bank):
    acc = bank.open_account()
    bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "7391"})
    code = bank.sms.last_code()
    assert bank.c.post("/api/login/verify", json={"acc_no": acc, "otp": code}).status_code == 200
    assert bank.c.post("/api/login/verify", json={"acc_no": acc, "otp": code}).status_code == 400


def test_otp_expires(bank, app):
    acc = bank.open_account()
    bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "7391"})
    with app.state.db.SessionLocal() as db:
        for ch in db.scalars(select(OTPChallenge)):
            ch.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    r = bank.c.post("/api/login/verify", json={"acc_no": acc, "otp": bank.sms.last_code()})
    assert r.status_code == 400 and "expired" in r.json()["detail"]


def test_otp_guessing_is_capped(bank):
    acc = bank.open_account()
    bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "7391"})
    real = bank.sms.last_code()
    wrong = "000000" if real != "000000" else "111111"
    statuses = [bank.c.post("/api/login/verify", json={"acc_no": acc, "otp": wrong}).status_code for _ in range(6)]
    assert statuses[:5] == [400] * 5 and statuses[5] == 429
    # The challenge is burned: even the real code no longer works.
    assert bank.c.post("/api/login/verify", json={"acc_no": acc, "otp": real}).status_code == 400


def test_otp_is_not_returned_when_demo_mode_is_off(bank):
    r = bank.c.post("/api/otp/send", json={"phone": "9123456789"})
    assert r.status_code == 200 and "dev_otp" not in r.json()


def test_sms_outage_without_demo_mode_fails_closed(bank):
    bank.sms.fail = True
    r = bank.c.post("/api/otp/send", json={"phone": "9123456789"})
    assert r.status_code == 503 and "dev_otp" not in r.json()


def test_otp_send_is_rate_limited(bank):
    codes = [bank.c.post("/api/otp/send", json={"phone": "9123456789"}).status_code for _ in range(7)]
    assert 429 in codes


def test_pins_and_otps_are_never_stored_in_plain_text(bank, app):
    acc = bank.open_account(pin="7391")
    bank.c.post("/api/login/otp", json={"acc_no": acc, "pin": "7391"})
    code = bank.sms.last_code()
    with app.state.db.SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.account_no == acc))
        assert "7391" not in account.pin_hash and account.pin_hash.startswith("$2")
        assert all(code not in ch.code_hash for ch in db.scalars(select(OTPChallenge)))


def test_tampered_and_expired_tokens_are_rejected(bank, settings):
    acc, h = bank.funded("0")
    token = h["Authorization"].split()[1]
    claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    forged = jwt.encode({**claims, "sub": "999"}, "wrong-key", algorithm="HS256")
    expired = jwt.encode(
        {**claims, "exp": datetime.now(UTC) - timedelta(seconds=1)}, settings.secret_key, algorithm="HS256"
    )
    for bad in (forged, expired, "not-a-jwt"):
        assert bank.c.get("/api/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_pin_change_signs_out_existing_sessions(bank):
    acc, h = bank.funded("0")
    r = bank.c.put("/api/account/update", json={"current_pin": "7391", "new_pin": "5832"}, headers=h)
    assert r.status_code == 200 and r.json()["reauth"] is True
    assert bank.c.get("/api/me", headers=h).status_code == 401
    bank.login(acc, pin="5832")


def test_profile_change_requires_current_pin(bank):
    _, h = bank.funded("0")
    r = bank.c.put("/api/account/update", json={"current_pin": "1000", "new_email": "x@evil.com"}, headers=h)
    assert r.status_code == 401


def test_session_is_bound_to_its_own_account(bank):
    _, h_a = bank.funded("100")
    acc_b, _ = bank.funded("100")
    me = bank.c.get("/api/me", headers=h_a).json()
    assert me["accountNo"] != acc_b
    assert me["phone"].startswith("******")  # phone is masked in responses


def test_kyc_rejects_non_pdf_and_ignores_client_filename(bank, settings):
    _, h = bank.funded("0")
    pdf = b"%PDF-1.4 fake"
    files = {
        "aadhaar": ("../../main.py", pdf, "application/pdf"),
        "pan": ("pan.pdf", pdf, "application/pdf"),
        "address_proof": ("addr.pdf", b"<script>alert(1)</script>", "application/pdf"),
    }
    assert bank.c.post("/api/docs/upload", files=files, headers=h).status_code == 415
    files["address_proof"] = ("addr.pdf", pdf, "application/pdf")
    r = bank.c.post("/api/docs/upload", files=files, headers=h)
    assert r.status_code == 200 and r.json()["kycStatus"] == "pending_review"
    stored = list(settings.uploads_dir.iterdir())
    assert len(stored) == 3 and all(len(p.stem) == 32 for p in stored)


def test_kyc_size_limit(bank):
    _, h = bank.funded("0")
    big = b"%PDF-" + b"0" * (2 * 1024 * 1024)
    files = {k: (f"{k}.pdf", big, "application/pdf") for k in ("aadhaar", "pan", "address_proof")}
    assert bank.c.post("/api/docs/upload", files=files, headers=h).status_code == 413


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"

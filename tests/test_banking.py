"""Business rules, money arithmetic and concurrency."""

import threading
from dataclasses import replace

import pytest
from sqlalchemy import select

from resan import services
from resan.models import Account, Transaction


def test_signup_and_login_flow(bank):
    acc, h = bank.funded("0")
    me = bank.c.get("/api/me", headers=h).json()
    assert me["accountNo"] == acc and me["accountType"] == "Savings Account" and me["balance"] == 0


def test_signup_needs_the_otp_sent_to_that_phone(bank):
    bank.c.post("/api/otp/send", json={"phone": "9111111111"})
    code = bank.sms.last_code()
    body = {"name": "Asha Rao", "age": 30, "email": "a@b.com", "phone": "9222222222", "pin": "7391", "otp": code}
    assert bank.c.post("/api/account/create", json=body).status_code == 400


@pytest.mark.parametrize(
    "override,fragment",
    [
        ({"age": -5}, "greater than or equal"),
        ({"name": ""}, "name"),
        ({"name": "<script>"}, "name"),
        ({"pin": "1234"}, "too easy"),
        ({"pin": "12a4"}, "4 digits"),
        ({"email": "nope"}, "email"),
        ({"phone": "12345"}, "mobile"),
    ],
)
def test_signup_validation(bank, override, fragment):
    bank.c.post("/api/otp/send", json={"phone": "9333333333"})
    body = {"name": "Asha Rao", "age": 30, "email": "a@b.com", "phone": "9333333333", "pin": "7391",
            "otp": bank.sms.last_code(), **override}  # fmt: skip
    r = bank.c.post("/api/account/create", json=body)
    assert r.status_code == 422 and fragment.lower() in r.json()["detail"].lower()


def test_minor_needs_guardian(bank):
    bank.c.post("/api/otp/send", json={"phone": "9444444444"})
    body = {"name": "Riya Rao", "age": 15, "email": "r@b.com", "phone": "9444444444", "pin": "7391",
            "otp": bank.sms.last_code()}  # fmt: skip
    assert bank.c.post("/api/account/create", json=body).status_code == 422
    acc = bank.open_account(name="Riya Rao", age=15, guardian_name="Meena Rao", guardian_age=42,
                            guardian_relation="Mother", guardian_phone="9555555555")  # fmt: skip
    h = bank.login(acc)
    me = bank.c.get("/api/me", headers=h).json()
    assert me["accountType"] == "Minor Account" and me["guardian"]["name"] == "Meena Rao"


def test_money_is_exact(bank):
    _, h = bank.funded(None)
    for _ in range(3):
        bank.c.post("/api/deposit", json={"amount": "1.10"}, headers=h)
    bank.c.post("/api/deposit", json={"amount": 0.1}, headers=h)
    bank.c.post("/api/deposit", json={"amount": 0.2}, headers=h)
    assert bank.balance(h) == 3.60  # v2 produced 3.5999999999999996


@pytest.mark.parametrize("amount", [0, -5, "1.005", "abc", 10_000_001])
def test_invalid_amounts_rejected(bank, amount):
    _, h = bank.funded("10")
    assert bank.c.post("/api/deposit", json={"amount": amount}, headers=h).status_code in (400, 422)


def test_withdraw_and_insufficient_funds(bank):
    _, h = bank.funded("100")
    assert bank.c.post("/api/withdraw", json={"amount": "40.50"}, headers=h).json()["new_balance"] == 59.5
    r = bank.c.post("/api/withdraw", json={"amount": 60}, headers=h)
    assert r.status_code == 400 and "Insufficient" in r.json()["detail"]
    assert bank.balance(h) == 59.5


def test_transfer_moves_money_and_links_both_legs(bank, app):
    a, ha = bank.funded("500")
    b, hb = bank.funded("0")
    r = bank.c.post("/api/transfer", json={"to_acc": b, "amount": 120}, headers=ha)
    assert r.status_code == 200 and r.json()["new_balance"] == 380
    assert bank.balance(hb) == 120
    out = bank.c.get("/api/transactions", headers=ha).json()["transactions"][0]
    inn = bank.c.get("/api/transactions", headers=hb).json()["transactions"][0]
    assert out["type"] == "Transfer Out" and inn["type"] == "Transfer In"
    assert out["reference"] == inn["reference"] and out["counterparty"] == b and inn["counterparty"] == a


@pytest.mark.parametrize("target,status", [("self", 400), ("RS-ZZZZZZZZ", 404), ("bad", 422)])
def test_transfer_rejections(bank, target, status):
    acc, h = bank.funded("100")
    to = acc if target == "self" else target
    assert bank.c.post("/api/transfer", json={"to_acc": to, "amount": 10}, headers=h).status_code == status
    assert bank.balance(h) == 100


def test_daily_limit(bank, app):
    app.state.settings = replace(app.state.settings, daily_debit_limit_paise=1000_00)
    _, h = bank.funded("5000")
    assert bank.c.post("/api/withdraw", json={"amount": 600}, headers=h).status_code == 200
    r = bank.c.post("/api/withdraw", json={"amount": 500}, headers=h)
    assert r.status_code == 400 and "400.00" in r.json()["detail"]


def test_minor_has_lower_daily_limit(bank):
    _, h = bank.funded("20000", name="Riya Rao", age=15, guardian_name="Meena Rao", guardian_age=42,
                       guardian_relation="Mother", guardian_phone="9555555555")  # fmt: skip
    assert bank.c.post("/api/withdraw", json={"amount": 10001}, headers=h).status_code == 400


def test_cannot_close_with_money_in_account(bank):
    _, h = bank.funded("250")
    r = bank.c.request("DELETE", "/api/account/close", json={"pin": "7391", "confirm": "DELETE"}, headers=h)
    assert r.status_code == 409 and "250.00" in r.json()["detail"]
    bank.c.post("/api/withdraw", json={"amount": 250}, headers=h)
    r = bank.c.request("DELETE", "/api/account/close", json={"pin": "7391", "confirm": "DELETE"}, headers=h)
    assert r.status_code == 200
    assert bank.c.get("/api/me", headers=h).status_code == 401  # session ends with the account


def test_closed_account_cannot_receive_or_log_in(bank):
    a, ha = bank.funded("100")
    b, hb = bank.funded("0")
    bank.c.request("DELETE", "/api/account/close", json={"pin": "7391", "confirm": "DELETE"}, headers=hb)
    assert bank.c.post("/api/transfer", json={"to_acc": b, "amount": 10}, headers=ha).status_code == 404
    assert bank.c.post("/api/login/otp", json={"acc_no": b, "pin": "7391"}).status_code == 401


def test_stats_counts_open_accounts(bank):
    bank.funded("100")
    bank.funded("50", name="Riya Rao", age=15, guardian_name="Meena Rao", guardian_age=42,
                guardian_relation="Mother", guardian_phone="9555555555")  # fmt: skip
    assert bank.c.get("/api/stats").json() == {
        "total_accounts": 2, "total_balance": 150.0, "savings_accounts": 1, "minor_accounts": 1,
    }  # fmt: skip


def test_transactions_pagination(bank):
    _, h = bank.funded(None)
    for i in range(1, 6):
        bank.c.post("/api/deposit", json={"amount": i}, headers=h)
    page = bank.c.get("/api/transactions?limit=2&offset=1", headers=h).json()["transactions"]
    assert len(page) == 2


# ── Concurrency: the ledger must never go negative or lose money ─────────────


def _account(app, acc_no):
    with app.state.db.SessionLocal() as db:
        return db.scalar(select(Account).where(Account.account_no == acc_no))


def _run_parallel(n, fn):
    barrier = threading.Barrier(n)
    results, lock = [], threading.Lock()

    def worker():
        barrier.wait()
        try:
            fn()
            outcome = "ok"
        except services.BankError as e:
            outcome = e.status
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_withdrawals_cannot_overdraw(bank, app):
    acc, _ = bank.funded("1000")
    settings = app.state.settings

    def withdraw_100():
        with app.state.db.SessionLocal() as db:
            account = db.scalar(select(Account).where(Account.account_no == acc))
            services.withdraw(db, account, 100_00, settings)

    results = _run_parallel(25, withdraw_100)
    assert results.count("ok") == 10 and results.count(400) == 15
    assert _account(app, acc).balance_paise == 0


def test_concurrent_transfers_conserve_money(bank, app):
    a, _ = bank.funded("1000")
    b, _ = bank.funded("1000")
    settings = app.state.settings

    def ping_pong(src, dst):
        def run():
            with app.state.db.SessionLocal() as db:
                account = db.scalar(select(Account).where(Account.account_no == src))
                services.transfer(db, account, dst, 37_00, settings)

        return run

    fns = [ping_pong(a, b), ping_pong(b, a)] * 15
    it = iter(fns)
    lock = threading.Lock()

    def next_fn():
        with lock:
            f = next(it)
        f()

    _run_parallel(len(fns), next_fn)
    total = _account(app, a).balance_paise + _account(app, b).balance_paise
    assert total == 2000_00
    with app.state.db.SessionLocal() as db:
        legs = db.scalars(select(Transaction).where(Transaction.type.in_(("Transfer In", "Transfer Out")))).all()
        assert len(legs) % 2 == 0
        assert sum(t.amount_paise for t in legs if t.type == "Transfer In") == sum(
            t.amount_paise for t in legs if t.type == "Transfer Out"
        )

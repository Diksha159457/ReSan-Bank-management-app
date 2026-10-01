import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from resan.app import create_app
from resan.config import Settings
from resan.db import Base


class FakeSMS:
    """Captures OTPs instead of sending them; ``fail=True`` simulates an SMS outage."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def send(self, phone, body):
        if self.fail:
            return False
        self.sent.append((phone, body))
        return True

    def last_code(self) -> str:
        return self.sent[-1][1].split("OTP is ")[1][:6]


@pytest.fixture
def settings(tmp_path):
    # Run against Postgres in CI by setting TEST_DATABASE_URL; SQLite file otherwise.
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    return replace(
        Settings(),
        database_url=url,
        secret_key="test-secret-key-that-is-long-enough-for-hs256",
        uploads_dir=tmp_path / "uploads",
        demo_mode=False,
        otp_resend_cooldown_seconds=0,
    )


@pytest.fixture
def sms():
    return FakeSMS()


@pytest.fixture
def app(settings, sms):
    application = create_app(settings, sms=sms)
    yield application
    Base.metadata.drop_all(application.state.db.engine)
    application.state.db.engine.dispose()


@pytest.fixture
def client(app):
    return TestClient(app)


class Bank:
    """Test helper that drives the API like the real frontend does."""

    def __init__(self, client: TestClient, sms: FakeSMS):
        self.c = client
        self.sms = sms
        self._phone = 9_000_000_000

    def next_phone(self) -> str:
        self._phone += 1
        return str(self._phone)

    def open_account(self, name="Asha Rao", age=30, pin="7391", **extra) -> str:
        phone = extra.pop("phone", None) or self.next_phone()
        assert self.c.post("/api/otp/send", json={"phone": phone}).status_code == 200
        body = {"name": name, "age": age, "email": "asha@example.com", "phone": phone, "pin": pin,
                "otp": self.sms.last_code(), **extra}  # fmt: skip
        r = self.c.post("/api/account/create", json=body)
        assert r.status_code == 201, r.json()
        return r.json()["accountNo"]

    def login(self, acc_no: str, pin="7391") -> dict:
        r = self.c.post("/api/login/otp", json={"acc_no": acc_no, "pin": pin})
        assert r.status_code == 200, r.json()
        r = self.c.post("/api/login/verify", json={"acc_no": acc_no, "otp": self.sms.last_code()})
        assert r.status_code == 200, r.json()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def funded(self, rupees="1000", **kw) -> tuple[str, dict]:
        acc = self.open_account(**kw)
        h = self.login(acc, kw.get("pin", "7391"))
        if rupees and rupees != "0":
            assert self.c.post("/api/deposit", json={"amount": rupees}, headers=h).status_code == 200
        return acc, h

    def balance(self, headers) -> float:
        return self.c.get("/api/me", headers=headers).json()["balance"]


@pytest.fixture
def bank(client, sms):
    return Bank(client, sms)

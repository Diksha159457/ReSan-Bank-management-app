# 🏦 ReSan Private Bank

[![CI](https://github.com/Diksha159457/ReSan-Bank-management-app/actions/workflows/ci.yml/badge.svg)](https://github.com/Diksha159457/ReSan-Bank-management-app/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Postgres](https://img.shields.io/badge/Postgres%20%7C%20SQLite-4169E1?logo=postgresql&logoColor=white)

A full-stack banking app built with **FastAPI**, **SQLAlchemy** and a vanilla-JS frontend with a dark-gold theme. The interesting part is the backend: it's built so that the things that go wrong in real banking systems can't happen here, and every one of them has a test.

**Live demo:** https://resan-bank-management-app-2.onrender.com

> 🧪 **This is a portfolio project, not a real bank.** In demo mode the OTP is shown on screen, and you should only upload sample PDFs, never real Aadhaar or PAN documents.

## ✨ Features

| Feature | Details |
|---|---|
| Account opening | Phone verified by OTP; minor accounts (10–17) require guardian details |
| Sign-in | Account number + PIN, then an OTP, which issues a 15-minute session token |
| Deposits, withdrawals, transfers | Exact paise arithmetic, all-or-nothing transfers, daily limits |
| Statement | Paginated history with transaction IDs, running balance and transfer counterparty |
| KYC upload | PDF-only, size-checked, queued as *pending review* |
| Profile & PIN change | Requires re-entering the current PIN; changing the PIN signs out every session |
| Account closure | Only allowed once the balance is ₹0 |
| Dark / light theme, responsive layout | |

## 🔐 Security model

This version (v3) is a rewrite of v2's backend. Each row below was a real weakness in v2, and each fix is pinned by a test in [`tests/test_security.py`](tests/test_security.py) or [`tests/test_banking.py`](tests/test_banking.py).

| Threat | v2 behaviour | v3 defence |
|---|---|---|
| **Skipping 2FA** | Every money endpoint accepted *account no. + PIN*; the OTP was decorative | PIN + OTP mints a signed JWT. Every money endpoint requires it, and it's bound to one account |
| **PIN brute force** | 9,000 possible PINs, no limit: a test script found one in **29 s** | 5 wrong PINs lock the account for 15 min; login is rate-limited per account and per IP |
| **Account probing** | Different errors for "no such account" and "wrong PIN" | Identical message, plus a dummy bcrypt check so response times match too |
| **OTP abuse** | Never expired, unlimited guesses, sent to anyone who asked, no rate limit | 5-minute expiry, single use, 5 attempts then burned; resend cooldown and send rate limits. Codes come from `secrets` and are stored only as an HMAC |
| **OTP leaked in API** | Returned in the response whenever SMS wasn't configured | Only in an explicit `DEMO_MODE`, labelled in the UI. With `DEMO_MODE=false` an SMS outage fails closed (503) |
| **Credential storage** | PINs in plain text in a JSON file; PIN in the URL query string | bcrypt-hashed PINs; credentials never appear in URLs; phone numbers masked in responses |
| **Stale sessions** | — | Changing the PIN or closing the account bumps a token version, which instantly revokes old tokens |
| **Overdraft via race** | Read balance → check → write, so parallel requests could all pass the check | One conditional `UPDATE … WHERE balance >= amount` inside a transaction, plus a DB `CHECK (balance >= 0)` and row locks on Postgres. A test fires 25 parallel ₹100 withdrawals at ₹1,000: exactly 10 succeed |
| **Floating-point money** | `1.10 × 3 = 3.2999999999999996` | Integer paise end to end; API inputs parsed as `Decimal` with ≤ 2 decimal places |
| **Half-done transfers** | Debit and credit written separately to a JSON file | Both legs commit in one DB transaction and share a reference ID |
| **Malicious uploads** | Trusted client `Content-Type`; client filename used on disk | PDF magic-byte check, 2 MB cap, random server-side filenames |
| **Closing with money in it** | Balance silently deleted | Refused (409) until the balance is ₹0 |
| **XSS** | Server data rendered with `innerHTML` unescaped | Everything rendered from the API is HTML-escaped; security headers (`X-Frame-Options`, `nosniff`, `no-store`) on every response |

**Deliberately out of scope:** rate-limiter state is in memory (fine for one instance; use Redis behind several). Real KYC review, interest, and statements as PDF aren't implemented.

## 🧱 Architecture

```
frontend/ (vanilla JS)  ──Bearer JWT──▶  resan/api.py      routes, auth dependency, rate limits
                                             │
                                         resan/schemas.py  Pydantic validation (names, phones, PINs, Decimal amounts)
                                             │
                                         resan/services.py business rules: OTPs, lockout, atomic money movement
                                             │
                                         resan/models.py   SQLAlchemy: accounts, guardians, transactions, otp_challenges, kyc_documents
                                             │
                                  SQLite (local, default)  ·  Postgres (DATABASE_URL)
```

## 🚀 Run it

```bash
git clone https://github.com/Diksha159457/ReSan-Bank-management-app.git
cd ReSan-Bank-management-app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py                      # http://localhost:8000  (SQLite, demo mode)
```

With Postgres, via Docker:

```bash
docker compose up --build           # app + Postgres 16 at http://localhost:8000
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///resan.db` | Use Postgres in production (`postgres://…` URLs from Render are accepted) |
| `SECRET_KEY` | random per boot | **Set this in production**, or every restart signs everyone out |
| `DEMO_MODE` | `true` if Twilio isn't configured | Show OTPs on screen instead of sending SMS |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | — | Real SMS OTPs |
| `ACCESS_TOKEN_MINUTES` | `15` | Session length |
| `ALLOWED_ORIGINS` | none (same-origin only) | Comma-separated origins if the frontend is hosted elsewhere |

### Deploying on Render

Start command: `gunicorn -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT`. Set `SECRET_KEY`. For data that survives redeploys, create a free Render Postgres instance and set `DATABASE_URL` to its internal URL. On SQLite, Render's disk is wiped on every deploy.

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest --cov=resan        # 54 tests, ~93% coverage
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/resan_test pytest   # same suite on Postgres
```

CI runs ruff, then the full suite on **both SQLite and Postgres 16**. It then builds the Docker image, boots it with Postgres, and checks that an unauthenticated deposit is rejected.

## 📡 API

Interactive docs at **`/docs`**. Endpoints marked 🔒 need `Authorization: Bearer <token>`.

| Method | Endpoint | |
|---|---|---|
| GET | `/api/health` | Status, demo-mode flag |
| GET | `/api/stats` | Aggregate counts for the landing page |
| POST | `/api/otp/send` | Signup OTP for a phone number |
| POST | `/api/account/create` | Open an account |
| POST | `/api/login/otp` | Check PIN, send login OTP |
| POST | `/api/login/verify` | Check OTP, get a session token |
| GET | `/api/me` 🔒 | Account details |
| POST | `/api/deposit` · `/api/withdraw` 🔒 | `{"amount": "1000.50"}` |
| POST | `/api/transfer` 🔒 | `{"to_acc": "RS-XXXXXXXX", "amount": "250"}` |
| GET | `/api/transactions?limit=&offset=` 🔒 | Statement |
| PUT | `/api/account/update` 🔒 | Needs `current_pin` |
| DELETE | `/api/account/close` 🔒 | Needs `pin` and `confirm: "DELETE"` |
| POST | `/api/docs/upload` 🔒 | KYC PDFs |

## 🔮 Roadmap

- [x] Database backend (SQLite / Postgres)
- [x] Token authentication with real 2FA
- [ ] Alembic migrations
- [ ] Redis-backed rate limiting for multi-instance deploys
- [ ] Admin console for KYC review
- [ ] Email notifications for transfers and logins

## 🛠 Tech stack

**Backend:** Python · FastAPI · SQLAlchemy 2 · Pydantic v2 · bcrypt · PyJWT · Gunicorn/Uvicorn
**Frontend:** HTML5 · CSS3 (custom properties, glassmorphism) · vanilla ES6+
**Infra:** Docker · docker-compose · GitHub Actions · Render

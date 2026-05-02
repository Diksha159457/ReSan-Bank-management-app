# 🏦 ReSan Private Bank — Management System
## Live url
https://resan-bank-management-app-2.onrender.com
A full-stack banking application built with **FastAPI** + **Vanilla JS**, featuring a luxury dark-gold aesthetic and complete banking operations.

## ✨ Features

| Feature | Description |
|---|---|
| Account Creation | Register with OTP verification, supports minor accounts with guardian |
| Secure Login | Account number + PIN + OTP two-factor authentication |
| Deposits & Withdrawals | Real-time balance updates with transaction logging |
| Fund Transfers | Instant transfers between ReSan accounts |
| Transaction History | Full statement with chronological audit trail |
| Document Upload | KYC verification — Aadhaar, PAN, Address Proof |
| Profile Management | Update name, email, and PIN |
| Account Closure | Permanent closure with double-confirmation |
| Dark / Light Theme | Toggle with persistent preference |
| Responsive Design | Works on desktop, tablet, and mobile |

## 🛠 Tech Stack

**Backend:** Python · FastAPI · Pydantic · Uvicorn  
**Frontend:** HTML5 · CSS3 (custom properties, glassmorphism) · Vanilla ES6+ JS  
**Storage:** JSON flat-file (drop-in replaceable with PostgreSQL)  
**Deployment:** Railway · Render · Heroku · Any ASGI host

## 🚀 Local Setup

```bash
# 1. Clone
git clone https://github.com/Diksha159457/ReSan-Bank--management-app.git
cd ReSan-Bank--management-app

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python main.py

# Open http://localhost:8000
```

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/otp/send` | Send OTP |
| POST | `/api/account/create` | Create account |
| POST | `/api/login/otp` | Login OTP |
| POST | `/api/login/verify` | Verify login OTP |
| POST | `/api/account/details` | Get account info |
| POST | `/api/deposit` | Deposit funds |
| POST | `/api/withdraw` | Withdraw funds |
| POST | `/api/transfer` | Transfer between accounts |
| GET | `/api/transactions/{acc}` | Transaction history |
| PUT | `/api/account/update` | Update profile |
| DELETE | `/api/account/close` | Close account |
| POST | `/api/docs/upload` | Upload KYC documents |
| GET | `/api/stats` | Bank-wide statistics |

Interactive API docs at **`/docs`** (Swagger UI).

## 🗂 Project Structure

```
resan-bank/
├── main.py              # FastAPI backend (all API routes)
├── requirements.txt     # Python dependencies
├── resan_data.json      # Account data (auto-created)
├── Procfile             # Deployment config
├── uploads/             # KYC document storage (auto-created)
└── frontend/
    ├── index.html       # Single-page application shell
    ├── style.css        # Luxury dark/light theme styles
    └── app.js           # All client-side logic
```

## 🚀 Deploy to Render (Free)

1. Push to GitHub
2. Go to [render.com](https://render.com) → New Web Service
3. Connect repo, set **Start Command**: `python main.py`
4. Deploy — live in ~2 minutes

## 🔮 Future Roadmap

- [ ] PostgreSQL / SQLite backend
- [ ] JWT authentication with refresh tokens
- [ ] Email + SMS notifications via Twilio / SendGrid
- [ ] Admin dashboard with analytics
- [ ] Multi-currency support
- [ ] Loan management module

---

Built with FastAPI · Designed for production · Resume-ready project


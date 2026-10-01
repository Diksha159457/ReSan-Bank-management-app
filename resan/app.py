"""Application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import security
from .api import register_error_handlers, router
from .config import BASE_DIR, Settings
from .db import Database
from .ratelimit import RateLimiter
from .sms import SMSSender, build_sender

VERSION = "3.0.0"


def create_app(settings: Settings | None = None, sms: SMSSender | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(title="ReSan Private Bank", version=VERSION)
    app.state.settings = settings
    app.state.db = Database(settings.database_url)
    app.state.db.create_all()
    app.state.sms = sms or build_sender(settings)
    app.state.limiter = RateLimiter()
    app.state.dummy_hash = security.hash_pin("0000")

    if settings.allowed_origins:
        # Tokens travel in the Authorization header, so cookies/credentials are never needed.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_, exc: RequestValidationError):
        # Return the first human-readable message instead of Pydantic's raw error list.
        first = exc.errors()[0] if exc.errors() else {}
        msg = str(first.get("msg", "Invalid request.")).removeprefix("Value error, ")
        field = ".".join(str(p) for p in first.get("loc", [])[1:])
        return JSONResponse(status_code=422, content={"detail": msg, "field": field})

    register_error_handlers(app)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=BASE_DIR / "frontend", html=True), name="frontend")
    return app

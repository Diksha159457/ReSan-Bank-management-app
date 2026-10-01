"""OTP delivery by SMS (Twilio). Never logs the code itself."""

from __future__ import annotations

import logging
from typing import Protocol

from .config import Settings

log = logging.getLogger("resan.sms")


class SMSSender(Protocol):
    def send(self, phone: str, body: str) -> bool: ...


class NullSender:
    """Used when Twilio isn't configured: nothing is sent."""

    def send(self, phone: str, body: str) -> bool:
        return False


class TwilioSender:
    def __init__(self, settings: Settings) -> None:
        from twilio.rest import Client

        self._client = Client(settings.twilio_sid, settings.twilio_token)
        self._from = settings.twilio_from

    def send(self, phone: str, body: str) -> bool:
        to = "+91" + phone if len(phone) == 10 else "+" + phone
        try:
            self._client.messages.create(body=body, from_=self._from, to=to)
            return True
        except Exception as exc:  # pragma: no cover - network
            log.error("SMS delivery failed: %s", type(exc).__name__)
            return False


def build_sender(settings: Settings) -> SMSSender:
    if settings.sms_configured:
        try:
            return TwilioSender(settings)
        except ImportError:  # pragma: no cover
            log.warning("twilio package not installed; SMS disabled")
    return NullSender()

"""SMS delivery. Replace ConsoleSmsSender with your provider's HTTP API."""

import logging
from typing import Protocol

log = logging.getLogger("loan_api.sms")


class SmsSender(Protocol):
    def send(self, phone: str, text: str) -> None: ...


class ConsoleSmsSender:
    """Development sender: writes the message to the server log."""

    def send(self, phone: str, text: str) -> None:
        log.info("SMS to %s: %s", phone, text)


_sender: SmsSender = ConsoleSmsSender()


def get_sender() -> SmsSender:
    return _sender


def set_sender(sender: SmsSender) -> None:
    global _sender
    _sender = sender

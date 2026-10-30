"""Dry-run channel: prints messages instead of sending them."""
from __future__ import annotations

import itertools
import logging
import sys

from ..models import DeliveryResult, RenderedMessage
from .base import Channel

log = logging.getLogger("com2wa.console")


class ConsoleChannel(Channel):
    name = "console"

    def __init__(self, printer=print) -> None:
        self._printer = printer
        self._ids = itertools.count(1)
        self.sent: list[RenderedMessage] = []

    def send(self, msg: RenderedMessage) -> DeliveryResult:
        self.sent.append(msg)
        out = f"--- WhatsApp -> +{msg.to} [{msg.lang}] ({msg.template}) ---\n{msg.text}\n"
        # A Windows cp1252 console cannot print emojis; degrade instead of failing delivery.
        for enc in (None, getattr(sys.stdout, "encoding", None), "ascii"):
            try:
                self._printer(out if enc is None else out.encode(enc, "replace").decode(enc))
                break
            except (UnicodeEncodeError, LookupError):
                continue
        return DeliveryResult(ok=True, provider_id=f"console-{next(self._ids)}")

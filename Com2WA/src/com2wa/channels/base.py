"""Channel interface. Implement `send` to add another provider (Twilio, Telegram, e-mail, ...)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import DeliveryResult, RenderedMessage


class Channel(ABC):
    name: str = "base"

    @abstractmethod
    def send(self, msg: RenderedMessage) -> DeliveryResult:
        """Deliver one message. Must not raise; report failures via DeliveryResult."""

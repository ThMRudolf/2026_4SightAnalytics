"""Core data types shared by rules, manager, outbox and channels."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any

_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    CRITICAL = 30

    @classmethod
    def parse(cls, value: "Severity | str | int") -> "Severity":
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[str(value).strip().upper()]


class AlertState(Enum):
    OK = "ok"
    PENDING = "pending"      # violated, waiting for debounce
    ACTIVE = "active"        # alert raised
    CLEARING = "clearing"    # back in range, waiting for debounce


class AlertKind(str, Enum):
    ALERT = "alert"
    RESOLVED = "resolved"
    REMINDER = "reminder"
    SUPPRESSED_SUMMARY = "suppressed_summary"


@dataclass(frozen=True)
class Recipient:
    name: str
    phone: str
    lang: str = "en"
    groups: tuple[str, ...] = ("*",)
    min_severity: Severity = Severity.WARNING
    quiet_hours: tuple[int, int] | None = None   # (start_hour, end_hour) local time

    def __post_init__(self) -> None:
        if not _PHONE_RE.match(self.phone.replace(" ", "")):
            raise ValueError(f"Recipient {self.name!r}: invalid phone {self.phone!r} (use E.164, e.g. +5215512345678)")

    @property
    def wa_id(self) -> str:
        """Phone number in the format the WhatsApp API expects (digits only)."""
        return re.sub(r"\D", "", self.phone)

    def subscribed_to(self, group: str) -> bool:
        return "*" in self.groups or group == "*" or group in self.groups

    def in_quiet_hours(self, ts: float) -> bool:
        if not self.quiet_hours:
            return False
        start, end = self.quiet_hours
        hour = datetime.fromtimestamp(ts).hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end   # wraps midnight, e.g. (22, 6)


@dataclass
class Alert:
    kind: AlertKind
    source: str
    signal: str
    severity: Severity
    group: str
    value: Any = None
    limit_text: str = ""
    unit: str = ""
    label: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.source}/{self.signal}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "source": self.source, "signal": self.signal,
                "severity": self.severity.name, "group": self.group, "value": self.value,
                "limit": self.limit_text, "unit": self.unit, "context": self.context,
                "timestamp": self.timestamp}


@dataclass
class RenderedMessage:
    """A channel-ready message for one recipient. Serialisable (stored in the outbox)."""
    to: str                 # wa_id, digits only
    lang: str               # catalog language: de / es / en
    template: str           # WhatsApp template name
    template_lang: str      # WhatsApp template language code
    params: list[str]       # body parameters {{1}}..{{n}}
    text: str               # full human-readable text (console / free-text mode)
    alert_key: str = ""
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RenderedMessage":
        return cls(**d)


@dataclass
class DeliveryResult:
    ok: bool
    provider_id: str | None = None
    retryable: bool = False
    error: str | None = None

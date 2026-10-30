"""Rule types: decide whether a signal value is in violation.

A rule matches signals by (source, signal) glob patterns, e.g. signal="servo_temp_j*".
Timing behaviour (debounce, cooldown, reminders) is handled by AlertManager.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from .models import Severity

_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


@dataclass
class Rule:
    source: str
    signal: str
    severity: Severity = Severity.WARNING
    group: str = "*"
    unit: str = ""
    label: dict[str, str] = field(default_factory=dict)   # per-language signal label
    debounce_s: float | None = None        # None -> manager default
    cooldown_s: float | None = None
    notify_on_clear: bool | None = None
    repeat_every_s: float | None = None    # reminder while active; None/0 = off

    def matches(self, source: str, signal: str) -> bool:
        return fnmatchcase(source, self.source) and fnmatchcase(signal, self.signal)

    def is_violated(self, value: Any, active: bool) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def limit_text(self) -> str:
        return ""


@dataclass
class ThresholdRule(Rule):
    op: str = ">"
    limit: float = 0.0
    hysteresis: float = 0.0

    def __post_init__(self) -> None:
        if self.op not in _OPS:
            raise ValueError(f"Unsupported operator {self.op!r}; use one of {list(_OPS)}")
        if self.hysteresis < 0:
            raise ValueError("hysteresis must be >= 0")

    def is_violated(self, value: Any, active: bool) -> bool:
        if value is None:
            return False
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        limit = self.limit
        if active and self.hysteresis:
            # Once active, the value must go back past limit -/+ hysteresis to clear.
            limit = limit - self.hysteresis if self.op in (">", ">=") else limit + self.hysteresis
        return _OPS[self.op](v, limit)

    def limit_text(self) -> str:
        return f"{self.op} {self.limit:g}{(' ' + self.unit) if self.unit else ''}"


@dataclass
class NonZeroRule(Rule):
    """Violated when the value is truthy, e.g. error/warn codes. Strings in `ok_values` count as OK."""
    ok_values: tuple[str, ...] = ("", "0", "NO ACTIVE ALARMS", "NONE", "UNAVAILABLE")

    def is_violated(self, value: Any, active: bool) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().upper() not in {o.upper() for o in self.ok_values}
        return bool(value)

    def limit_text(self) -> str:
        return "!= 0"


@dataclass
class EqualsRule(Rule):
    """Violated when value == `equals` (or != when negate=True). Useful for states like STOPPED."""
    equals: Any = None
    negate: bool = False

    def is_violated(self, value: Any, active: bool) -> bool:
        if value is None:
            return False
        a, b = value, self.equals
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.strip().upper(), b.strip().upper()
        return (a != b) if self.negate else (a == b)

    def limit_text(self) -> str:
        return f"{'!=' if self.negate else '=='} {self.equals}"


_RULE_TYPES: dict[str, type[Rule]] = {
    "threshold": ThresholdRule,
    "nonzero": NonZeroRule,
    "equals": EqualsRule,
}


def rule_from_dict(d: dict[str, Any]) -> Rule:
    d = dict(d)
    rtype = d.pop("type", "threshold")
    cls = _RULE_TYPES.get(rtype)
    if cls is None:
        raise ValueError(f"Unknown rule type {rtype!r}; use one of {list(_RULE_TYPES)}")
    if "severity" in d:
        d["severity"] = Severity.parse(d["severity"])
    if "ok_values" in d:
        d["ok_values"] = tuple(d["ok_values"])
    known = set(cls.__dataclass_fields__)
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"Rule {d.get('source')}/{d.get('signal')}: unknown keys {sorted(unknown)}")
    return cls(**d)

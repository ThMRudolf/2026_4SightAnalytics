"""AlertManager – turns a stream of signal values into deduplicated, routed alerts.

Call `update()` from any poll loop (thread-safe, no network I/O). Per (rule, source,
signal) a small state machine runs:

    OK ──violated──▶ PENDING ──debounce──▶ ACTIVE ──ok──▶ CLEARING ──debounce──▶ OK
         (alert sent on entering ACTIVE, "resolved" on returning to OK)
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import i18n
from .channels.base import Channel
from .models import Alert, AlertKind, AlertState, Recipient, Severity
from .outbox import Outbox
from .rules import Rule

log = logging.getLogger("com2wa")


@dataclass
class _Track:
    state: AlertState = AlertState.OK
    since: float = 0.0              # entered current state
    last_alert: float | None = None  # last time an ALERT was actually sent
    last_reminder: float = 0.0
    notified: bool = False          # the current ACTIVE episode produced a message
    last_value: Any = None


class AlertManager:
    def __init__(self, rules: Iterable[Rule], recipients: Iterable[Recipient], channel: Channel, *,
                 outbox_path: str = "com2wa_outbox.db",
                 debounce_s: float = 5.0, cooldown_s: float = 600.0, notify_on_clear: bool = True,
                 rate_limit_per_min: int = 20, template_lang_codes: dict[str, str] | None = None,
                 clock: Callable[[], float] = time.time, start_worker: bool = True,
                 outbox_options: dict | None = None) -> None:
        self.rules = list(rules)
        self.recipients = list(recipients)
        self.debounce_s = debounce_s
        self.cooldown_s = cooldown_s
        self.notify_on_clear = notify_on_clear
        self.rate_limit_per_min = rate_limit_per_min
        self.template_lang_codes = template_lang_codes or {}
        self._clock = clock
        self._lock = threading.Lock()
        self._tracks: dict[tuple[int, str, str], _Track] = {}
        self._sent_times: deque[float] = deque()
        self._dropped = 0
        self.outbox = Outbox(outbox_path, channel, clock=clock, **(outbox_options or {}))
        if start_worker:
            self.outbox.start()

    @classmethod
    def from_config(cls, path: str, **overrides: Any) -> "AlertManager":
        from .config import build_manager
        return build_manager(path, **overrides)

    # ------------------------------------------------------------------ API
    def update(self, source: str, signal: str, value: Any,
               context: dict[str, Any] | None = None) -> list[Alert]:
        """Feed one signal value. Returns the alerts emitted by this call (mostly empty)."""
        emitted: list[Alert] = []
        now = self._clock()
        with self._lock:
            for idx, rule in enumerate(self.rules):
                if not rule.matches(source, signal):
                    continue
                track = self._tracks.setdefault((idx, source, signal), _Track(since=now))
                track.last_value = value
                kind = self._step(rule, track, value, now)
                if kind is not None:
                    alert = Alert(kind=kind, source=source, signal=signal, severity=rule.severity,
                                  group=rule.group, value=value, limit_text=rule.limit_text(),
                                  unit=rule.unit, label=rule.label, context=dict(context or {}),
                                  timestamp=now)
                    emitted.extend(self._dispatch(alert, now))
        return emitted

    def update_many(self, source: str, values: dict[str, Any],
                    context: dict[str, Any] | None = None) -> list[Alert]:
        out: list[Alert] = []
        for signal, value in values.items():
            out.extend(self.update(source, signal, value, context))
        return out

    def notify(self, source: str, signal: str, message_value: Any,
               severity: Severity | str = Severity.WARNING, group: str = "*") -> list[Alert]:
        """Send an ad-hoc alert without a rule (still routed, rate-limited and queued)."""
        now = self._clock()
        alert = Alert(kind=AlertKind.ALERT, source=source, signal=signal,
                      severity=Severity.parse(severity), group=group, value=message_value, timestamp=now)
        with self._lock:
            return self._dispatch(alert, now)

    def active_alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"source": s, "signal": sig, "value": t.last_value, "since": t.since,
                     "severity": self.rules[i].severity.name}
                    for (i, s, sig), t in self._tracks.items()
                    if t.state in (AlertState.ACTIVE, AlertState.CLEARING)]

    def close(self, flush_timeout_s: float = 5.0) -> None:
        self.outbox.close(flush_timeout_s)

    def __enter__(self) -> "AlertManager":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -------------------------------------------------------------- internals
    def _opt(self, rule_value: Any, default: Any) -> Any:
        return default if rule_value is None else rule_value

    def _step(self, rule: Rule, t: _Track, value: Any, now: float) -> AlertKind | None:
        debounce = self._opt(rule.debounce_s, self.debounce_s)
        active = t.state in (AlertState.ACTIVE, AlertState.CLEARING)
        violated = rule.is_violated(value, active)

        if t.state is AlertState.OK:
            if violated:
                t.state, t.since = AlertState.PENDING, now
                if debounce <= 0:
                    return self._activate(rule, t, now)
        elif t.state is AlertState.PENDING:
            if not violated:
                t.state, t.since = AlertState.OK, now
            elif now - t.since >= debounce:
                return self._activate(rule, t, now)
        elif t.state is AlertState.ACTIVE:
            if not violated:
                t.state, t.since = AlertState.CLEARING, now
                if debounce <= 0:
                    return self._clear(rule, t, now)
            else:
                repeat = rule.repeat_every_s or 0
                if repeat > 0 and t.notified and now - t.last_reminder >= repeat:
                    t.last_reminder = now
                    return AlertKind.REMINDER
        elif t.state is AlertState.CLEARING:
            if violated:
                t.state = AlertState.ACTIVE   # keep original 'since'
            elif now - t.since >= debounce:
                return self._clear(rule, t, now)
        return None

    def _activate(self, rule: Rule, t: _Track, now: float) -> AlertKind | None:
        t.state, t.since = AlertState.ACTIVE, now
        cooldown = self._opt(rule.cooldown_s, self.cooldown_s)
        if t.last_alert is not None and now - t.last_alert < cooldown:
            t.notified = False
            log.info("Alert %s/%s suppressed by cooldown", rule.source, rule.signal)
            return None
        t.notified, t.last_alert, t.last_reminder = True, now, now
        return AlertKind.ALERT

    def _clear(self, rule: Rule, t: _Track, now: float) -> AlertKind | None:
        t.state, t.since = AlertState.OK, now
        was_notified, t.notified = t.notified, False
        if was_notified and self._opt(rule.notify_on_clear, self.notify_on_clear):
            return AlertKind.RESOLVED
        return None

    def _rate_ok(self, now: float) -> bool:
        while self._sent_times and now - self._sent_times[0] >= 60.0:
            self._sent_times.popleft()
        return self.rate_limit_per_min <= 0 or len(self._sent_times) < self.rate_limit_per_min

    def _dispatch(self, alert: Alert, now: float) -> list[Alert]:
        if not self._rate_ok(now):
            self._dropped += 1
            log.warning("Rate limit reached, dropped %s %s", alert.kind.value, alert.key)
            return []
        out: list[Alert] = []
        if self._dropped:
            summary = Alert(kind=AlertKind.SUPPRESSED_SUMMARY, source="Com2WA", signal="rate_limit",
                            severity=Severity.WARNING, group="*", value=self._dropped, timestamp=now)
            self._dropped = 0
            self._enqueue(summary, now)
            out.append(summary)
        self._enqueue(alert, now)
        out.append(alert)
        return out

    def _enqueue(self, alert: Alert, now: float) -> None:
        self._sent_times.append(now)
        targets = [r for r in self.recipients
                   if r.subscribed_to(alert.group)
                   and alert.severity >= r.min_severity
                   and (alert.severity >= Severity.CRITICAL or not r.in_quiet_hours(now))]
        if not targets:
            log.info("No recipient for %s %s (%s, group %s)", alert.kind.value, alert.key,
                     alert.severity.name, alert.group)
        for r in targets:
            self.outbox.enqueue(i18n.render(alert, r, self.template_lang_codes))
        log.info("%s %s=%s → %d recipient(s)", alert.kind.value.upper(), alert.key, alert.value, len(targets))

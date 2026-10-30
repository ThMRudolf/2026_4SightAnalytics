from __future__ import annotations

import pytest

from com2wa import AlertManager, DeliveryResult, Recipient, Severity, ThresholdRule
from com2wa.channels.base import Channel


class FakeClock:
    def __init__(self, t: float = 1_760_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeChannel(Channel):
    name = "fake"

    def __init__(self) -> None:
        self.sent = []
        self.results: list[DeliveryResult] = []   # consumed first, then ok

    def send(self, msg):
        self.sent.append(msg)
        if self.results:
            return self.results.pop(0)
        return DeliveryResult(ok=True, provider_id=f"wamid.{len(self.sent)}")


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def channel():
    return FakeChannel()


@pytest.fixture
def recipients():
    return [Recipient("Ana", "+5215500000001", lang="es", groups=("haas",), min_severity=Severity.INFO)]


@pytest.fixture
def spindle_rule():
    return ThresholdRule(source="HAAS_TM1P", signal="spindle_speed", op=">", limit=4000, hysteresis=200,
                         debounce_s=2, cooldown_s=60, unit="rpm", severity=Severity.INFO, group="haas")


@pytest.fixture
def make_manager(clock, channel, recipients):
    created = []

    def _make(rules, recips=None, **kw):
        kw.setdefault("debounce_s", 0)
        kw.setdefault("cooldown_s", 0)
        m = AlertManager(rules, recipients if recips is None else recips, channel,
                         outbox_path=":memory:", clock=clock, start_worker=False, **kw)
        created.append(m)
        return m

    yield _make
    for m in created:
        m.close(flush_timeout_s=0)

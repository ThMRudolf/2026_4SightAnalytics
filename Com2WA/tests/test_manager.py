from com2wa import AlertKind, NonZeroRule, Recipient, Severity, ThresholdRule
from com2wa.adapters import feed_haas, feed_ufactory


def kinds(alerts):
    return [a.kind for a in alerts]


def run(mgr, clock, source, signal, values, dt=1.0):
    out = []
    for v in values:
        out += mgr.update(source, signal, v)
        clock.advance(dt)
    return out


def test_spindle_demo_alert_once_and_resolved(make_manager, clock, spindle_rule, channel):
    mgr = make_manager([spindle_rule])
    ramp = [0, 2000, 4000, 4500, 4500, 4500, 4500, 4300, 3900, 3700, 3000, 3000, 3000]
    alerts = run(mgr, clock, "HAAS_TM1P", "spindle_speed", ramp)
    assert kinds(alerts) == [AlertKind.ALERT, AlertKind.RESOLVED]
    mgr.outbox.process_due()
    assert [m.template for m in channel.sent] == ["machine_alert", "machine_alert_resolved"]
    assert channel.sent[0].lang == "es" and "4500 rpm" in channel.sent[0].text


def test_debounce_filters_spikes(make_manager, clock, spindle_rule):
    mgr = make_manager([spindle_rule])               # debounce 2 s
    alerts = run(mgr, clock, "HAAS_TM1P", "spindle_speed", [4500, 3000, 4500, 3000, 4500, 3000])
    assert alerts == []


def test_hysteresis_prevents_flapping(make_manager, clock, spindle_rule):
    spindle_rule.debounce_s = 0
    mgr = make_manager([spindle_rule])
    alerts = run(mgr, clock, "HAAS_TM1P", "spindle_speed", [4100, 3950, 4050, 3900, 4100])
    assert kinds(alerts) == [AlertKind.ALERT]


def test_cooldown_suppresses_realert_and_its_resolve(make_manager, clock, spindle_rule):
    spindle_rule.debounce_s = 0
    mgr = make_manager([spindle_rule])               # cooldown 60 s
    a = run(mgr, clock, "HAAS_TM1P", "spindle_speed", [4500, 3000, 4500, 3000])
    assert kinds(a) == [AlertKind.ALERT, AlertKind.RESOLVED]
    clock.advance(60)
    a = run(mgr, clock, "HAAS_TM1P", "spindle_speed", [4500, 3000])
    assert kinds(a) == [AlertKind.ALERT, AlertKind.RESOLVED]


def test_reminder(make_manager, clock, spindle_rule):
    spindle_rule.debounce_s = 0
    spindle_rule.repeat_every_s = 10
    mgr = make_manager([spindle_rule])
    a = run(mgr, clock, "HAAS_TM1P", "spindle_speed", [4500] * 25)
    assert kinds(a) == [AlertKind.ALERT, AlertKind.REMINDER, AlertKind.REMINDER]


def test_routing_by_group_severity_and_quiet_hours(make_manager, clock, channel):
    rule = NonZeroRule(source="UF850", signal="warn_code", severity=Severity.WARNING, group="ufactory")
    recips = [
        Recipient("haas-only", "+4915100000001", groups=("haas",), min_severity=Severity.INFO),
        Recipient("crit-only", "+4915100000002", groups=("ufactory",), min_severity=Severity.CRITICAL),
        Recipient("all", "+4915100000003", lang="de", groups=("*",), min_severity=Severity.WARNING),
        Recipient("sleeping", "+4915100000004", groups=("ufactory",), quiet_hours=(0, 24)),
    ]
    mgr = make_manager([rule], recips)
    mgr.update("UF850", "warn_code", 5)
    mgr.outbox.process_due()
    assert [m.to for m in channel.sent] == ["4915100000003"]
    assert channel.sent[0].params[0] == "WARNUNG"


def test_rate_limit_with_summary(make_manager, clock, channel):
    rule = ThresholdRule(source="S", signal="v*", op=">", limit=0, group="haas", severity=Severity.WARNING)
    mgr = make_manager([rule], rate_limit_per_min=3, notify_on_clear=False)
    for i in range(6):
        mgr.update("S", f"v{i}", 1)
    clock.advance(61)
    out = mgr.update("S", "v99", 1)
    assert kinds(out) == [AlertKind.SUPPRESSED_SUMMARY, AlertKind.ALERT]
    assert out[0].value == 3


def test_ufactory_adapter_temperature(make_manager, clock):
    rule = ThresholdRule(source="UF850", signal="servo_temp_j*", op=">", limit=55, hysteresis=3,
                         group="haas", severity=Severity.WARNING)
    mgr = make_manager([rule])
    out = []
    for t in [50, 56, 57, 53, 51]:
        out += feed_ufactory(mgr, {"connected": True, "temps": [30, 30, t, 30, 30, 30], "joints": [0] * 6})
        clock.advance(1)
    assert [(a.kind, a.signal) for a in out] == [(AlertKind.ALERT, "servo_temp_j3"),
                                                 (AlertKind.RESOLVED, "servo_temp_j3")]


def test_haas_adapter_context_and_tool_wear(make_manager, clock, channel):
    rule = ThresholdRule(source="HAAS_TM1P", signal="tool_wear_len_t*", op=">=", limit=0.05,
                         unit="mm", group="haas", label={"es": "Desgaste de longitud T{idx}"})
    mgr = make_manager([rule])
    feed_haas(mgr, {"spindle_rpm": 0, "tool": {"tool_number": 4}}, tool_lib=[{"t": 4, "lWear": 0.051}])
    mgr.outbox.process_due()
    assert channel.sent[0].params[2] == "Desgaste de longitud T4"
    assert "tool=4" in channel.sent[0].params[3]

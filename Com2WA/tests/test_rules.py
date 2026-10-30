import pytest

from com2wa import EqualsRule, NonZeroRule, Severity, ThresholdRule, rule_from_dict


def test_threshold_hysteresis():
    r = ThresholdRule(source="*", signal="x", op=">", limit=4000, hysteresis=200)
    assert not r.is_violated(4000, active=False)
    assert r.is_violated(4001, active=False)
    assert r.is_violated(3900, active=True)        # still above 3800
    assert not r.is_violated(3800, active=True)


def test_threshold_lower_limit_hysteresis():
    r = ThresholdRule(source="*", signal="x", op="<", limit=10, hysteresis=2)
    assert r.is_violated(9, active=False)
    assert r.is_violated(11, active=True)
    assert not r.is_violated(12, active=True)


def test_threshold_ignores_non_numeric():
    r = ThresholdRule(source="*", signal="x", op=">", limit=1)
    assert not r.is_violated(None, False)
    assert not r.is_violated("UNAVAILABLE", False)


def test_nonzero_rule():
    r = NonZeroRule(source="*", signal="*")
    assert not r.is_violated(0, False)
    assert r.is_violated(3, False)
    assert not r.is_violated("NO ACTIVE ALARMS", False)
    assert r.is_violated("1010 SPINDLE OVERLOAD", False)


def test_equals_rule():
    r = EqualsRule(source="*", signal="run_status", equals="STOPPED")
    assert r.is_violated("stopped", False)
    assert not r.is_violated("ACTIVE", False)


def test_glob_matching():
    r = ThresholdRule(source="UF850", signal="servo_temp_j*", limit=55)
    assert r.matches("UF850", "servo_temp_j3")
    assert not r.matches("UF850", "joint_usage_j3")
    assert not r.matches("HAAS_TM1P", "servo_temp_j3")


def test_rule_from_dict():
    r = rule_from_dict({"source": "A", "signal": "b", "type": "threshold", "op": ">=", "limit": 0.05,
                        "severity": "critical"})
    assert isinstance(r, ThresholdRule) and r.severity is Severity.CRITICAL
    with pytest.raises(ValueError):
        rule_from_dict({"source": "A", "signal": "b", "typo_key": 1})
    with pytest.raises(ValueError):
        rule_from_dict({"source": "A", "signal": "b", "op": "=>"})

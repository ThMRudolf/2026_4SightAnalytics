"""Adapter for the UFactory 850 backend (IIoT_UFactory/backend/backend.py).

Integration in `_poll_loop()` right before updating the cache:

    with _state_lock:
        _cached_state.update(update)
    feed_ufactory(ALERTS, update)                     # <-- add this line
"""
from __future__ import annotations

import math
from typing import Any

SOURCE = "UF850"


def joint_usage_pct(angle_rad: float) -> float:
    """Same mapping as the dashboard bar: -π..+π → 0..100 %."""
    return max(0.0, min(100.0, (angle_rad + math.pi) / (2 * math.pi) * 100.0))


def ufactory_signals(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("connected", True):
        return {"connected": False}
    sig: dict[str, Any] = {
        "connected": True,
        "error_code": state.get("errorCode"),
        "warn_code": state.get("warnCode"),
        "motion_state": state.get("motionState"),
    }
    for i, t in enumerate(state.get("temps") or [], start=1):
        sig[f"servo_temp_j{i}"] = t
    for i, a in enumerate(state.get("joints") or [], start=1):
        sig[f"joint_usage_j{i}"] = round(joint_usage_pct(a), 1)
    return sig


def feed_ufactory(manager, state: dict[str, Any], source: str = SOURCE):
    """Push one UFactory poll cycle into the AlertManager. Signals without a rule are ignored."""
    return manager.update_many(source, ufactory_signals(state))

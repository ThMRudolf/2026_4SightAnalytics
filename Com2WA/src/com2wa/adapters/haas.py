"""Adapter for the Haas TM-1P MTConnect backend (IIoT_Haas/Backend/main.py).

Integration in `poller_loop()` right after parsing:

    current_data          = parse_current(root_c)
    axes_data, rpm_sample = parse_samples(root_s)
    rpm = rpm_sample or current_data.get("spindle_rpm")
    feed_haas(ALERTS, current_data, rpm=rpm)          # <-- add this line
"""
from __future__ import annotations

from typing import Any, Iterable

SOURCE = "HAAS_TM1P"


def haas_signals(current_data: dict[str, Any], rpm: float | None = None,
                 tool_lib: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Map parse_current() output (+ optional tool library) to Com2WA signal names."""
    sig: dict[str, Any] = {
        "spindle_speed": rpm if rpm is not None else current_data.get("spindle_rpm"),
        "active_alarms": current_data.get("active_alarms"),
        "run_status": current_data.get("run_status"),
        "controller_mode": current_data.get("controller_mode"),
    }
    # Tool library entries as used by the dashboard: {"t": 4, "lWear": 0.012, "dWear": 0.004}
    for tool in tool_lib or []:
        t = tool.get("t")
        if t is None:
            continue
        if tool.get("lWear") is not None:
            sig[f"tool_wear_len_t{t}"] = tool["lWear"]
        if tool.get("dWear") is not None:
            sig[f"tool_wear_dia_t{t}"] = tool["dWear"]
    return sig


def feed_haas(manager, current_data: dict[str, Any], rpm: float | None = None,
              tool_lib: Iterable[dict[str, Any]] | None = None, source: str = SOURCE):
    """Push one Haas poll cycle into the AlertManager. Signals without a rule are ignored."""
    ctx = {}
    if current_data.get("active_program"):
        ctx["prog"] = current_data["active_program"]
    tool_no = (current_data.get("tool") or {}).get("tool_number")
    if tool_no:
        ctx["tool"] = tool_no
    return manager.update_many(source, haas_signals(current_data, rpm, tool_lib), context=ctx)

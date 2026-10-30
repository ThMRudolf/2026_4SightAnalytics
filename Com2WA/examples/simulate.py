"""Replay synthetic Haas / UFactory signals through Com2WA with a simulated clock.

    python examples/simulate.py                 # console output, no WhatsApp needed
    python examples/simulate.py --config config.yaml   # use your own config / channel
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from com2wa import build_manager
from com2wa.adapters import feed_haas, feed_ufactory

HERE = Path(__file__).resolve().parent.parent


class SimClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 6, 10, 0).timestamp()   # daytime: outside quiet hours

    def __call__(self) -> float:
        return self.t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.example.yaml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    clock = SimClock()
    outbox = str(Path(tempfile.mkdtemp()) / "sim_outbox.db")
    mgr = build_manager(args.config, clock=clock, outbox_path=outbox, start_worker=False)

    def tick(dt: float = 1.0) -> None:
        clock.t += dt
        mgr.outbox.process_due()

    print("=== Haas spindle ramp 0 -> 4500 -> 3000 rpm (demo rule > 4000 rpm) ===")
    ramp = list(range(0, 4501, 500)) + [4500] * 4 + list(range(4500, 2999, -250)) + [3000] * 4
    for rpm in ramp:
        feed_haas(mgr, {"spindle_rpm": float(rpm), "active_program": "O01234", "tool": {"tool_number": 4}})
        tick()

    print("=== UFactory servo J3 temperature 50 -> 60 -> 50 °C (rule > 55 °C) ===")
    temps = [50 + i for i in range(11)] + [60] * 6 + [60 - i for i in range(11)] + [50] * 6
    for t in temps:
        feed_ufactory(mgr, {"connected": True, "errorCode": 0, "warnCode": 0,
                            "temps": [35, 36, t, 34, 33, 32], "joints": [0.0] * 6})
        tick()

    print("=== Haas tool wear T4 reaches 0.05 mm ===")
    feed_haas(mgr, {"spindle_rpm": 0.0}, tool_lib=[{"t": 4, "lWear": 0.051, "dWear": 0.01}])
    tick()

    summary = {}
    for row in mgr.outbox.rows():
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    print(f"Outbox: {summary}  (db: {outbox})")
    mgr.close(flush_timeout_s=0)


if __name__ == "__main__":
    main()

"""Demo: talk to a running com2wa-server over REST.

    com2wa-server --config config.yaml                       # terminal 1
    python examples/api_client_demo.py --key <COM2WA_API_KEY>  # terminal 2

Simulates a Haas spindle run-up to 4500 rpm and back, printing the alerts the server raises.
"""
from __future__ import annotations

import argparse
import os
import time

from com2wa.client import Com2WAClient, Com2WAError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("COM2WA_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--key", default=os.environ.get("COM2WA_API_KEY"))
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    args = ap.parse_args()

    wa = Com2WAClient(args.url, api_key=args.key)
    try:
        h = wa.health()
    except OSError as e:
        raise SystemExit(f"Server not reachable at {args.url}: {e}")
    print(f"Connected: channel={h['channel']} rules={h['rules']} recipients={h['recipients']}")

    profile = [0, 1500, 3000, 4500, 4500, 4500, 4500, 4500, 3500, 3000, 3000, 3000, 3000]
    try:
        for rpm in profile:
            alerts = wa.signal("HAAS_TM1P", "spindle_speed", rpm, context={"prog": "DEMO"})
            note = ", ".join(f"{a['kind'].upper()} ({a['severity']})" for a in alerts) or "-"
            print(f"spindle_speed={rpm:>5} rpm  ->  {note}")
            time.sleep(args.interval)
    except Com2WAError as e:
        raise SystemExit(f"API error: {e}")

    print("\nLast messages:")
    for m in wa.messages(limit=5):
        print(f"  #{m['id']} {m['kind']:<9} {m['alert_key']:<28} {m['to_phone']}  {m['status']}")


if __name__ == "__main__":
    main()

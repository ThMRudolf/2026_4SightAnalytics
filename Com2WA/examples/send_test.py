"""Live test: send one alert + one resolved template to a single number via Meta Cloud API.

    python examples/send_test.py --lang es                        # number from WA_TEST_RECIPIENT
    python examples/send_test.py +5215512345678 --lang es
    python examples/send_test.py +5215512345678 --lang de --text   # free text (24 h window only)
    python examples/send_test.py --hello        # Meta's pre-approved hello_world (connectivity check)

Needs WA_TOKEN and WA_PHONE_NUMBER_ID in the environment or Com2WA/.env.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from com2wa import Alert, AlertKind, MetaCloudChannel, Recipient, RenderedMessage, Severity
from com2wa.config import load_env
from com2wa.i18n import render


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phone", nargs="?", help="default: WA_TEST_RECIPIENT from .env")
    ap.add_argument("--lang", default="en", choices=["de", "es", "en"])
    ap.add_argument("--text", action="store_true", help="send free text instead of templates")
    ap.add_argument("--hello", action="store_true", help="send Meta's hello_world template only")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)

    load_env(Path(__file__).resolve().parent.parent / ".env")
    phone = args.phone or os.environ.get("WA_TEST_RECIPIENT", "")
    missing = [k for k in ("WA_TOKEN", "WA_PHONE_NUMBER_ID") if "PASTE" in os.environ.get(k, "PASTE")]
    if missing or not phone or "PASTE" in phone:
        raise SystemExit(f"Fill in Com2WA/.env first: {', '.join(missing + ([] if phone and 'PASTE' not in phone else ['WA_TEST_RECIPIENT']))}")
    ch = MetaCloudChannel(os.environ.get("WA_TOKEN", ""), os.environ.get("WA_PHONE_NUMBER_ID", ""),
                          os.environ.get("WA_API_VERSION", "v23.0"), use_templates=not args.text)
    r = Recipient("test", phone, lang=args.lang, min_severity=Severity.INFO)
    if args.hello:
        res = ch.send(RenderedMessage(to=r.wa_id, lang="en", template="hello_world", template_lang="en_US",
                                      params=[], text="hello_world"))
        print(f"hello_world: ok={res.ok} id={res.provider_id} error={res.error}")
        return
    common = dict(source="HAAS_TM1P", signal="spindle_speed", severity=Severity.INFO, group="haas",
                  unit="rpm", limit_text="> 4000 rpm",
                  label={"de": "Spindeldrehzahl", "es": "Velocidad del husillo", "en": "Spindle speed"})
    for kind, value in ((AlertKind.ALERT, 4500), (AlertKind.RESOLVED, 3000)):
        msg = render(Alert(kind=kind, value=value, timestamp=time.time(), **common), r)
        res = ch.send(msg)
        print(f"{kind.value}: ok={res.ok} id={res.provider_id} error={res.error}")


if __name__ == "__main__":
    main()

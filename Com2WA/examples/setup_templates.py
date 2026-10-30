"""Submit the Com2WA message templates to Meta and show their approval status.

    python examples/setup_templates.py --status              # list templates + status
    python examples/setup_templates.py --submit --lang es    # submit es only
    python examples/setup_templates.py --submit              # submit de, es, en

Needs in Com2WA/.env: WA_TOKEN (with whatsapp_business_management permission – the
temporary token from "API Setup" has it) and WA_WABA_ID (WhatsApp Business Account ID,
shown on the same page).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

from com2wa.config import load_env
from com2wa.i18n import BODIES, DEFAULT_TEMPLATE_LANG_CODES, LANGS, TEMPLATE_ALERT, TEMPLATE_RESOLVED

EXAMPLES = {
    TEMPLATE_ALERT: {
        "de": ["WARNUNG", "HAAS_TM1P", "Spindeldrehzahl", "4500 rpm (Grenze > 4000 rpm)", "2026-10-04 10:00:00"],
        "es": ["ADVERTENCIA", "HAAS_TM1P", "Velocidad del husillo", "4500 rpm (límite > 4000 rpm)", "2026-10-04 10:00:00"],
        "en": ["WARNING", "HAAS_TM1P", "Spindle speed", "4500 rpm (limit > 4000 rpm)", "2026-10-04 10:00:00"],
    },
    TEMPLATE_RESOLVED: {
        "de": ["HAAS_TM1P", "Spindeldrehzahl", "3000 rpm", "2026-10-04 10:05:00"],
        "es": ["HAAS_TM1P", "Velocidad del husillo", "3000 rpm", "2026-10-04 10:05:00"],
        "en": ["HAAS_TM1P", "Spindle speed", "3000 rpm", "2026-10-04 10:05:00"],
    },
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--lang", choices=LANGS, action="append", help="repeatable; default all")
    args = ap.parse_args()

    load_env(Path(__file__).resolve().parent.parent / ".env")
    token, waba = os.environ.get("WA_TOKEN", ""), os.environ.get("WA_WABA_ID", "")
    version = os.environ.get("WA_API_VERSION", "v23.0")
    if not token or not waba or "PASTE" in token or "PASTE" in waba:
        sys.exit("Set WA_TOKEN and WA_WABA_ID in Com2WA/.env first.")
    url = f"https://graph.facebook.com/{version}/{waba}/message_templates"
    headers = {"Authorization": f"Bearer {token}"}

    if args.submit:
        for name in (TEMPLATE_ALERT, TEMPLATE_RESOLVED):
            for lang in args.lang or LANGS:
                body = {
                    "name": name,
                    "language": DEFAULT_TEMPLATE_LANG_CODES[lang],
                    "category": "UTILITY",
                    "components": [{"type": "BODY", "text": BODIES[name][lang],
                                    "example": {"body_text": [EXAMPLES[name][lang]]}}],
                }
                r = requests.post(url, json=body, headers=headers, timeout=15)
                data = r.json() if r.content else {}
                if r.ok:
                    print(f"submitted {name} [{lang}] -> id={data.get('id')} status={data.get('status')}")
                else:
                    err = data.get("error", {})
                    print(f"ERROR {name} [{lang}]: HTTP {r.status_code} "
                          f"{err.get('error_user_msg') or err.get('message')}")

    if args.status or not args.submit:
        r = requests.get(url, headers=headers, timeout=15,
                         params={"fields": "name,language,status,rejected_reason", "limit": 100})
        if not r.ok:
            sys.exit(f"HTTP {r.status_code}: {r.text[:300]}")
        rows = [t for t in r.json().get("data", []) if t["name"] in (TEMPLATE_ALERT, TEMPLATE_RESOLVED, "hello_world")]
        for t in sorted(rows, key=lambda t: (t["name"], t["language"])):
            reason = t.get("rejected_reason")
            print(f"{t['name']:<24} {t['language']:<6} {t['status']}"
                  f"{'  (' + reason + ')' if reason and reason != 'NONE' else ''}")


if __name__ == "__main__":
    main()

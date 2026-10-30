"""Message catalog (de/es/en) and rendering of Alerts into WhatsApp template messages.

The texts in BODIES must match the WhatsApp templates you submit to Meta for approval
(see README). Meta rejects templates whose body starts or ends with a variable, hence
the closing line.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import Alert, AlertKind, Recipient, RenderedMessage, Severity

LANGS = ("de", "es", "en")
DEFAULT_LANG = "en"

TEMPLATE_ALERT = "machine_alert"          # params: severity, machine, signal, value, time
TEMPLATE_RESOLVED = "machine_alert_resolved"  # params: machine, signal, value, time

BODIES: dict[str, dict[str, str]] = {
    TEMPLATE_ALERT: {
        "de": "⚠️ {{1}}-Alarm an {{2}}\nSignal: {{3}}\nWert: {{4}}\nZeit: {{5}}\n\nGesendet von Com2WA Prozessüberwachung.",
        "es": "⚠️ Alerta {{1}} en {{2}}\nSeñal: {{3}}\nValor: {{4}}\nHora: {{5}}\n\nEnviado por el monitoreo de procesos Com2WA.",
        "en": "⚠️ {{1}} alert on {{2}}\nSignal: {{3}}\nValue: {{4}}\nTime: {{5}}\n\nSent by Com2WA process monitoring.",
    },
    TEMPLATE_RESOLVED: {
        "de": "✅ Behoben an {{1}}\nSignal: {{2}}\nWert: {{3}}\nZeit: {{4}}\n\nGesendet von Com2WA Prozessüberwachung.",
        "es": "✅ Resuelto en {{1}}\nSeñal: {{2}}\nValor: {{3}}\nHora: {{4}}\n\nEnviado por el monitoreo de procesos Com2WA.",
        "en": "✅ Resolved on {{1}}\nSignal: {{2}}\nValue: {{3}}\nTime: {{4}}\n\nSent by Com2WA process monitoring.",
    },
}

SEVERITY_WORDS: dict[str, dict[Severity, str]] = {
    "de": {Severity.INFO: "INFO", Severity.WARNING: "WARNUNG", Severity.CRITICAL: "KRITISCH"},
    "es": {Severity.INFO: "INFO", Severity.WARNING: "ADVERTENCIA", Severity.CRITICAL: "CRÍTICA"},
    "en": {Severity.INFO: "INFO", Severity.WARNING: "WARNING", Severity.CRITICAL: "CRITICAL"},
}

PHRASES: dict[str, dict[str, str]] = {
    "reminder": {"de": "Erinnerung", "es": "recordatorio", "en": "reminder"},
    "suppressed": {
        "de": "{n} Meldungen wegen Ratenbegrenzung unterdrückt",
        "es": "{n} mensajes suprimidos por límite de envío",
        "en": "{n} messages suppressed by rate limit",
    },
    "rate_limit": {"de": "Ratenbegrenzung", "es": "Límite de envío", "en": "Rate limit"},
}

# WhatsApp template language codes; override via config `template_lang_codes`.
DEFAULT_TEMPLATE_LANG_CODES = {"de": "de", "es": "es", "en": "en"}

_TRAILING_DIGITS = re.compile(r"(\d+)$")


def _lang(lang: str) -> str:
    lang = (lang or DEFAULT_LANG).lower()[:2]
    return lang if lang in LANGS else DEFAULT_LANG


def _clean_param(text: str) -> str:
    # Meta rejects parameters with newlines, tabs or more than 4 consecutive spaces.
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    return re.sub(r" {4,}", "   ", text).strip() or "-"


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.5g}"
    return str(value)


def signal_label(alert: Alert, lang: str) -> str:
    template = alert.label.get(lang) or alert.label.get(DEFAULT_LANG) or alert.signal
    m = _TRAILING_DIGITS.search(alert.signal)
    try:
        return template.format(signal=alert.signal, idx=m.group(1) if m else "")
    except (KeyError, IndexError):
        return template


def value_text(alert: Alert, lang: str) -> str:
    if alert.kind is AlertKind.SUPPRESSED_SUMMARY:
        return PHRASES["suppressed"][lang].format(n=alert.value)
    parts = [format_value(alert.value)]
    if alert.unit:
        parts.append(alert.unit)
    text = " ".join(parts)
    if alert.limit_text and alert.kind is not AlertKind.RESOLVED:
        text += f" (limit {alert.limit_text})" if lang == "en" else \
                f" (Grenze {alert.limit_text})" if lang == "de" else f" (límite {alert.limit_text})"
    if alert.context:
        text += " | " + ", ".join(f"{k}={format_value(v)}" for k, v in alert.context.items())
    return text


def render(alert: Alert, recipient: Recipient,
           template_lang_codes: dict[str, str] | None = None) -> RenderedMessage:
    lang = _lang(recipient.lang)
    codes = {**DEFAULT_TEMPLATE_LANG_CODES, **(template_lang_codes or {})}
    ts = datetime.fromtimestamp(alert.timestamp).strftime("%Y-%m-%d %H:%M:%S")
    machine = alert.source
    signal = signal_label(alert, lang)
    if alert.kind is AlertKind.SUPPRESSED_SUMMARY:
        machine, signal = "Com2WA", PHRASES["rate_limit"][lang]

    if alert.kind is AlertKind.RESOLVED:
        template = TEMPLATE_RESOLVED
        params = [machine, signal, value_text(alert, lang), ts]
    else:
        template = TEMPLATE_ALERT
        sev = SEVERITY_WORDS[lang][alert.severity]
        if alert.kind is AlertKind.REMINDER:
            sev = f"{sev} ({PHRASES['reminder'][lang]})"
        params = [sev, machine, signal, value_text(alert, lang), ts]

    params = [_clean_param(p) for p in params]
    text = BODIES[template][lang]
    for i, p in enumerate(params, start=1):
        text = text.replace("{{%d}}" % i, p)

    return RenderedMessage(
        to=recipient.wa_id, lang=lang, template=template, template_lang=codes[lang],
        params=params, text=text, alert_key=alert.key, kind=alert.kind.value,
    )

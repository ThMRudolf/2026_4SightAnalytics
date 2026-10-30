"""YAML configuration loading. Secrets (token, phone-number-id) come from the environment only."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .channels import Channel, ConsoleChannel, MetaCloudChannel
from .models import Recipient, Severity
from .rules import Rule, rule_from_dict

log = logging.getLogger("com2wa.config")


def load_env(env_file: str | os.PathLike | None = None) -> None:
    """Load a .env file if python-dotenv is installed (optional dependency)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_file) if env_file else load_dotenv()


def load_config(path: str | os.PathLike) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return cfg


def parse_recipients(items: list[dict[str, Any]]) -> list[Recipient]:
    out = []
    for d in items or []:
        quiet = d.get("quiet_hours")
        out.append(Recipient(
            name=d.get("name", d["phone"]),
            phone=str(d["phone"]),
            lang=d.get("lang", "en"),
            groups=tuple(d.get("groups", ["*"])),
            min_severity=Severity.parse(d.get("min_severity", "WARNING")),
            quiet_hours=tuple(quiet) if quiet else None,
        ))
    return out


def parse_rules(items: list[dict[str, Any]]) -> list[Rule]:
    return [rule_from_dict(d) for d in items or []]


def build_channel(cfg: dict[str, Any]) -> Channel:
    kind = cfg.get("channel", "console")
    if kind == "console":
        return ConsoleChannel()
    if kind == "meta_cloud":
        meta = cfg.get("meta_cloud", {})
        return MetaCloudChannel(
            token=os.environ.get("WA_TOKEN", ""),
            phone_number_id=os.environ.get("WA_PHONE_NUMBER_ID", ""),
            api_version=os.environ.get("WA_API_VERSION", meta.get("api_version", "v23.0")),
            use_templates=meta.get("use_templates", True),
        )
    raise ValueError(f"Unknown channel {kind!r} (use 'console' or 'meta_cloud')")


def build_manager(path: str | os.PathLike, *, env_file: str | None = None, **overrides: Any):
    from .manager import AlertManager

    load_env(env_file or Path(path).with_name(".env"))
    cfg = load_config(path)
    defaults = cfg.get("defaults", {})
    outbox_path = cfg.get("outbox_path", "com2wa_outbox.db")
    if not os.path.isabs(outbox_path):
        outbox_path = str(Path(path).resolve().parent / outbox_path)
    kwargs: dict[str, Any] = dict(
        rules=parse_rules(cfg.get("rules", [])),
        recipients=parse_recipients(cfg.get("recipients", [])),
        channel=overrides.pop("channel", None) or build_channel(cfg),
        outbox_path=outbox_path,
        debounce_s=defaults.get("debounce_s", 5.0),
        cooldown_s=defaults.get("cooldown_s", 600.0),
        notify_on_clear=defaults.get("notify_on_clear", True),
        rate_limit_per_min=defaults.get("rate_limit_per_min", 20),
        template_lang_codes=cfg.get("template_lang_codes"),
        outbox_options=cfg.get("outbox"),
    )
    kwargs.update(overrides)
    mgr = AlertManager(**kwargs)
    log.info("Com2WA ready: %d rules, %d recipients, channel=%s",
             len(mgr.rules), len(mgr.recipients), mgr.outbox.channel.name)
    return mgr

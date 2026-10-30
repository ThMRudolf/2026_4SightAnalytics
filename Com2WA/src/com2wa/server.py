"""REST API for Com2WA, so dashboards, scripts and other languages can use the alert engine over HTTP.

Run:
    com2wa-server --config config.yaml                 # http://127.0.0.1:8090
    python -m com2wa.server --config config.yaml --host 0.0.0.0 --port 8090

Auth: if COM2WA_API_KEY is set (env or .env), every endpoint except /health needs the
header `X-API-Key: <key>`. See README for all endpoints and examples.
"""
from __future__ import annotations

import argparse
import dataclasses
import hmac
import logging
import os
from typing import Any

from flask import Flask, jsonify, request

from . import __version__
from .manager import AlertManager
from .models import Severity

log = logging.getLogger("com2wa.server")

API = "/api/v1"
MAX_BATCH = 1000
MAX_NAME_LEN = 100
_SCALARS = (int, float, str, bool, type(None))


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _mask_phone(phone: str) -> str:
    digits = phone.lstrip("+")
    return "+" + "*" * max(0, len(digits) - 4) + digits[-4:]


def _name(d: dict, key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip() or len(v) > MAX_NAME_LEN:
        raise ApiError(f"'{key}' must be a non-empty string (max {MAX_NAME_LEN} chars)")
    return v.strip()


def _value(v: Any, where: str) -> Any:
    if not isinstance(v, _SCALARS):
        raise ApiError(f"{where}: value must be a number, string, boolean or null")
    return v


def _context(d: dict) -> dict | None:
    ctx = d.get("context")
    if ctx is None:
        return None
    if not isinstance(ctx, dict) or not all(isinstance(v, _SCALARS) for v in ctx.values()):
        raise ApiError("'context' must be an object with scalar values")
    return ctx


def _rule_dict(rule) -> dict:
    d = dataclasses.asdict(rule)
    d["type"] = type(rule).__name__.replace("Rule", "").lower()
    d["severity"] = rule.severity.name
    return d


def create_app(manager: AlertManager, api_key: str | None = None,
               cors_origins: list[str] | None = None) -> Flask:
    app = Flask("com2wa")
    app.json.sort_keys = False
    cors = set(cors_origins or [])

    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return jsonify(error=str(e)), e.status

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify(error="not found"), 404

    @app.errorhandler(405)
    def _bad_method(_e):
        return jsonify(error="method not allowed"), 405

    @app.before_request
    def _auth():
        if request.method == "OPTIONS" or request.path == f"{API}/health" or not api_key:
            return None
        given = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(given.encode(), api_key.encode()):
            return jsonify(error="missing or invalid X-API-Key"), 401
        return None

    @app.after_request
    def _cors(resp):
        origin = request.headers.get("Origin")
        if origin and ("*" in cors or origin in cors):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    def _json_body() -> Any:
        body = request.get_json(silent=True)
        if body is None:
            raise ApiError("request body must be JSON (Content-Type: application/json)")
        return body

    # ------------------------------------------------------------------ routes
    @app.get(f"{API}/health")
    def health():
        worker = manager.outbox._thread
        return jsonify(status="ok", version=__version__, channel=manager.outbox.channel.name,
                       rules=len(manager.rules), recipients=len(manager.recipients),
                       worker_alive=bool(worker and worker.is_alive()),
                       auth=bool(api_key))

    @app.post(f"{API}/signals")
    def signals():
        """Single {source, signal, value}, batch {source, values:{...}} or a list of those."""
        body = _json_body()
        items = body if isinstance(body, list) else [body]
        updates: list[tuple[str, str, Any, dict | None]] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ApiError(f"item {i}: must be an object")
            source, ctx = _name(item, "source"), _context(item)
            if "values" in item:
                if not isinstance(item["values"], dict) or not item["values"]:
                    raise ApiError(f"item {i}: 'values' must be a non-empty object")
                for sig, val in item["values"].items():
                    if not isinstance(sig, str) or not sig or len(sig) > MAX_NAME_LEN:
                        raise ApiError(f"item {i}: invalid signal name {sig!r}")
                    updates.append((source, sig, _value(val, f"{source}/{sig}"), ctx))
            else:
                if "value" not in item:
                    raise ApiError(f"item {i}: needs 'value' (or 'values' for a batch)")
                sig = _name(item, "signal")
                updates.append((source, sig, _value(item["value"], f"{source}/{sig}"), ctx))
        if len(updates) > MAX_BATCH:
            raise ApiError(f"too many signals in one request (max {MAX_BATCH})", 413)
        alerts = []
        for source, sig, val, ctx in updates:
            alerts += manager.update(source, sig, val, ctx)
        return jsonify(accepted=len(updates), alerts=[a.to_dict() for a in alerts])

    @app.post(f"{API}/notify")
    def notify():
        """Ad-hoc message without a rule: {source, signal, message, severity?, group?}."""
        body = _json_body()
        if not isinstance(body, dict):
            raise ApiError("body must be an object")
        if "message" not in body:
            raise ApiError("'message' is required")
        try:
            severity = Severity.parse(body.get("severity", "WARNING"))
        except (KeyError, ValueError):
            raise ApiError(f"severity must be one of {[s.name for s in Severity]}")
        group = body.get("group", "*")
        if not isinstance(group, str):
            raise ApiError("'group' must be a string")
        alerts = manager.notify(_name(body, "source"), _name(body, "signal"),
                                _value(body["message"], "message"), severity=severity, group=group)
        return jsonify(alerts=[a.to_dict() for a in alerts]), 202

    @app.get(f"{API}/alerts/active")
    def active():
        return jsonify(alerts=manager.active_alerts())

    @app.get(f"{API}/messages")
    def messages():
        status = request.args.get("status")
        if status and status not in ("pending", "sent", "failed"):
            raise ApiError("status must be pending, sent or failed")
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 500))
        except ValueError:
            raise ApiError("limit must be an integer")
        rows = manager.outbox.rows(status=status, limit=limit, newest_first=True)
        for r in rows:
            r["to_phone"] = _mask_phone(r["to_phone"])
        return jsonify(messages=rows)

    @app.get(f"{API}/rules")
    def rules():
        return jsonify(rules=[_rule_dict(r) for r in manager.rules])

    @app.get(f"{API}/recipients")
    def recipients():
        return jsonify(recipients=[{
            "name": r.name, "phone": _mask_phone(r.phone), "lang": r.lang, "groups": list(r.groups),
            "min_severity": r.min_severity.name, "quiet_hours": r.quiet_hours,
        } for r in manager.recipients])

    return app


def main(argv: list[str] | None = None) -> None:
    from .config import build_manager, load_config, load_env

    ap = argparse.ArgumentParser(description="Com2WA REST API server")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", help="default from config api.host or 127.0.0.1")
    ap.add_argument("--port", type=int, help="default from config api.port or 8090")
    ap.add_argument("--dev", action="store_true", help="use Flask dev server instead of waitress")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    api_cfg = load_config(args.config).get("api", {}) or {}
    host = args.host or api_cfg.get("host", "127.0.0.1")
    port = args.port or int(api_cfg.get("port", 8090))
    manager = build_manager(args.config)        # also loads .env
    load_env()
    api_key = os.environ.get("COM2WA_API_KEY") or None
    if not api_key:
        log.warning("COM2WA_API_KEY not set – API is unauthenticated. Keep host=127.0.0.1 or set a key.")
    app = create_app(manager, api_key=api_key, cors_origins=api_cfg.get("cors_origins"))

    log.info("Com2WA API on http://%s:%d%s", host, port, API)
    try:
        if args.dev:
            app.run(host=host, port=port, threaded=True)
        else:
            try:
                from waitress import serve
            except ImportError:
                log.warning("waitress not installed – falling back to Flask dev server")
                app.run(host=host, port=port, threaded=True)
            else:
                serve(app, host=host, port=port, threads=8)
    finally:
        manager.close()


if __name__ == "__main__":
    main()

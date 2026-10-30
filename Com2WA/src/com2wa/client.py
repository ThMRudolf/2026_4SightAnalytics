"""Thin Python client for the Com2WA REST API (for processes that don't embed AlertManager).

    from com2wa.client import Com2WAClient
    wa = Com2WAClient("http://127.0.0.1:8090", api_key="...")
    wa.signal("HAAS_TM1P", "spindle_speed", 4520)
"""
from __future__ import annotations

from typing import Any

import requests


class Com2WAError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Com2WAClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8090", api_key: str | None = None,
                 timeout_s: float = 3.0, session: requests.Session | None = None) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.timeout = timeout_s
        self.session = session or requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        resp = self.session.request(method, self.base + path, timeout=self.timeout, **kw)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": resp.text[:200]}
        if not resp.ok:
            raise Com2WAError(resp.status_code, data.get("error", "unknown error"))
        return data

    def health(self) -> dict:
        return self._call("GET", "/health")

    def signal(self, source: str, signal: str, value: Any, context: dict | None = None) -> list[dict]:
        body = {"source": source, "signal": signal, "value": value}
        if context:
            body["context"] = context
        return self._call("POST", "/signals", json=body)["alerts"]

    def signals(self, source: str, values: dict[str, Any], context: dict | None = None) -> list[dict]:
        body: dict[str, Any] = {"source": source, "values": values}
        if context:
            body["context"] = context
        return self._call("POST", "/signals", json=body)["alerts"]

    def notify(self, source: str, signal: str, message: Any, severity: str = "WARNING",
               group: str = "*") -> list[dict]:
        return self._call("POST", "/notify", json={"source": source, "signal": signal, "message": message,
                                                   "severity": severity, "group": group})["alerts"]

    def active_alerts(self) -> list[dict]:
        return self._call("GET", "/alerts/active")["alerts"]

    def messages(self, status: str | None = None, limit: int = 50) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return self._call("GET", "/messages", params=params)["messages"]

    def rules(self) -> list[dict]:
        return self._call("GET", "/rules")["rules"]

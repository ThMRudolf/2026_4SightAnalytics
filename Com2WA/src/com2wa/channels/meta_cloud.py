"""WhatsApp Business Cloud API (Meta) channel.

Business-initiated messages need an approved template (use_templates=True, default).
Free-text messages (use_templates=False) only arrive inside a 24 h customer-service
window, i.e. after the recipient has written to your business number.
"""
from __future__ import annotations

import logging

import requests

from ..models import DeliveryResult, RenderedMessage
from .base import Channel

log = logging.getLogger("com2wa.meta")

GRAPH_URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"

# Meta error codes that are transient (rate limits / throughput / temporary errors).
RETRYABLE_ERROR_CODES = {1, 2, 4, 17, 80007, 130429, 131000, 131016, 131048, 131056}


class MetaCloudChannel(Channel):
    name = "meta_cloud"

    def __init__(self, token: str, phone_number_id: str, api_version: str = "v23.0",
                 use_templates: bool = True, timeout_s: float = 10.0,
                 session: requests.Session | None = None) -> None:
        if not token or not phone_number_id:
            raise ValueError("MetaCloudChannel needs WA_TOKEN and WA_PHONE_NUMBER_ID")
        self._url = GRAPH_URL.format(version=api_version, phone_number_id=phone_number_id)
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._use_templates = use_templates
        self._timeout = timeout_s
        self._session = session or requests.Session()

    def build_payload(self, msg: RenderedMessage) -> dict:
        base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": msg.to}
        if not self._use_templates:
            return {**base, "type": "text", "text": {"preview_url": False, "body": msg.text}}
        template: dict = {"name": msg.template, "language": {"code": msg.template_lang}}
        if msg.params:   # templates without variables (e.g. hello_world) take no components
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in msg.params],
            }]
        return {**base, "type": "template", "template": template}

    def send(self, msg: RenderedMessage) -> DeliveryResult:
        try:
            resp = self._session.post(self._url, json=self.build_payload(msg),
                                      headers=self._headers, timeout=self._timeout)
        except requests.RequestException as e:
            return DeliveryResult(ok=False, retryable=True, error=f"network: {type(e).__name__}: {e}")

        try:
            body = resp.json()
        except ValueError:
            body = {}

        if resp.ok:
            msg_id = (body.get("messages") or [{}])[0].get("id")
            return DeliveryResult(ok=True, provider_id=msg_id)

        err = body.get("error") or {}
        code = err.get("code")
        text = f"HTTP {resp.status_code} code={code}: {err.get('message') or resp.text[:200]}"
        retryable = resp.status_code == 429 or resp.status_code >= 500 or code in RETRYABLE_ERROR_CODES
        return DeliveryResult(ok=False, retryable=retryable, error=text)

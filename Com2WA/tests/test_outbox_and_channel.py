from unittest.mock import MagicMock

import requests

from com2wa import DeliveryResult, MetaCloudChannel, RenderedMessage
from com2wa.outbox import Outbox


def msg(**kw):
    base = dict(to="5215500000001", lang="es", template="machine_alert", template_lang="es",
                params=["INFO", "HAAS", "Spindle", "4500 rpm", "2026-10-04 10:00:00"], text="t",
                alert_key="HAAS/spindle_speed", kind="alert")
    base.update(kw)
    return RenderedMessage(**base)


def test_retry_then_success(channel, clock):
    ob = Outbox(":memory:", channel, clock=clock, base_backoff_s=5)
    channel.results = [DeliveryResult(ok=False, retryable=True, error="HTTP 429")]
    ob.enqueue(msg())
    assert ob.process_due() == 1
    assert ob.rows()[0]["status"] == "pending" and ob.rows()[0]["attempts"] == 1
    assert ob.process_due() == 0            # backoff not elapsed
    clock.advance(5)
    ob.process_due()
    row = ob.rows()[0]
    assert row["status"] == "sent" and row["provider_id"] == "wamid.2"
    ob.close(0)


def test_non_retryable_fails_immediately(channel, clock):
    ob = Outbox(":memory:", channel, clock=clock)
    channel.results = [DeliveryResult(ok=False, retryable=False, error="HTTP 400 code=132001")]
    ob.enqueue(msg())
    ob.process_due()
    assert ob.rows()[0]["status"] == "failed"
    ob.close(0)


def test_max_attempts(channel, clock):
    ob = Outbox(":memory:", channel, clock=clock, max_attempts=3, base_backoff_s=1)
    channel.results = [DeliveryResult(ok=False, retryable=True, error="500")] * 5
    ob.enqueue(msg())
    for _ in range(5):
        ob.process_due()
        clock.advance(100)
    row = ob.rows()[0]
    assert row["status"] == "failed" and row["attempts"] == 3
    ob.close(0)


def test_pending_survives_restart(tmp_path, channel, clock):
    db = str(tmp_path / "o.db")
    ob = Outbox(db, channel, clock=clock)
    ob.enqueue(msg())
    ob.close(0)
    ob2 = Outbox(db, channel, clock=clock)
    ob2.process_due()
    assert ob2.rows()[0]["status"] == "sent"
    ob2.close(0)


def _resp(status, body):
    r = MagicMock()
    r.status_code, r.ok = status, 200 <= status < 300
    r.json.return_value = body
    r.text = str(body)
    return r


def test_meta_payload_and_success():
    s = MagicMock()
    s.post.return_value = _resp(200, {"messages": [{"id": "wamid.ABC"}]})
    ch = MetaCloudChannel("tok", "123", "v23.0", session=s)
    res = ch.send(msg())
    assert res.ok and res.provider_id == "wamid.ABC"
    url, = s.post.call_args.args
    payload = s.post.call_args.kwargs["json"]
    assert url == "https://graph.facebook.com/v23.0/123/messages"
    assert payload["template"]["name"] == "machine_alert"
    assert payload["template"]["language"]["code"] == "es"
    assert [p["text"] for p in payload["template"]["components"][0]["parameters"]][3] == "4500 rpm"


def test_meta_error_classification():
    s = MagicMock()
    ch = MetaCloudChannel("tok", "123", session=s)
    s.post.return_value = _resp(400, {"error": {"code": 132001, "message": "template not found"}})
    assert ch.send(msg()).retryable is False
    s.post.return_value = _resp(400, {"error": {"code": 131056, "message": "pair rate limit"}})
    assert ch.send(msg()).retryable is True
    s.post.return_value = _resp(503, {})
    assert ch.send(msg()).retryable is True
    s.post.side_effect = requests.ConnectionError("down")
    r = ch.send(msg())
    assert not r.ok and r.retryable


def test_console_channel_survives_non_utf8_console():
    from com2wa import ConsoleChannel
    printed = []

    def cp1252_printer(text):
        text.encode("cp1252")          # raises on emojis, like a Windows console
        printed.append(text)

    res = ConsoleChannel(printer=cp1252_printer).send(msg(text="⚠️ Alerta"))
    assert res.ok and printed

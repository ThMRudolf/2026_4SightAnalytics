import pytest

from com2wa import Severity
from com2wa.client import Com2WAClient, Com2WAError
from com2wa.server import create_app

KEY = "test-key"
H = {"X-API-Key": KEY}


@pytest.fixture
def mgr(make_manager, spindle_rule):
    spindle_rule.debounce_s = 0
    return make_manager([spindle_rule])


@pytest.fixture
def api(mgr):
    return create_app(mgr, api_key=KEY, cors_origins=["http://dash.local"]).test_client()


def test_health_is_public(api):
    r = api.get("/api/v1/health")
    assert r.status_code == 200 and r.json["status"] == "ok" and r.json["auth"] is True


def test_auth_required(api):
    assert api.post("/api/v1/signals", json={}).status_code == 401
    assert api.get("/api/v1/rules", headers={"X-API-Key": "wrong"}).status_code == 401


def test_single_signal_raises_and_resolves(api, mgr, clock, channel):
    body = {"source": "HAAS_TM1P", "signal": "spindle_speed", "value": 4500, "context": {"prog": "O1"}}
    r = api.post("/api/v1/signals", json=body, headers=H)
    assert r.status_code == 200 and r.json["accepted"] == 1
    assert [a["kind"] for a in r.json["alerts"]] == ["alert"]
    assert r.json["alerts"][0]["severity"] == "INFO"

    active = api.get("/api/v1/alerts/active", headers=H).json["alerts"]
    assert active[0]["signal"] == "spindle_speed"

    clock.advance(1)
    r = api.post("/api/v1/signals", json={**body, "value": 3000}, headers=H)
    assert [a["kind"] for a in r.json["alerts"]] == ["resolved"]

    mgr.outbox.process_due()
    msgs = api.get("/api/v1/messages?status=sent", headers=H).json["messages"]
    assert len(msgs) == 2 and msgs[0]["to_phone"].endswith("0001") and "*" in msgs[0]["to_phone"]


def test_batch_and_list(api):
    r = api.post("/api/v1/signals", headers=H,
                 json={"source": "UF850", "values": {"servo_temp_j1": 40, "warn_code": 0}})
    assert r.json["accepted"] == 2
    r = api.post("/api/v1/signals", headers=H, json=[
        {"source": "A", "signal": "x", "value": 1},
        {"source": "B", "values": {"y": "RUN", "z": None}},
    ])
    assert r.json["accepted"] == 3


@pytest.mark.parametrize("body,msg", [
    ({"signal": "x", "value": 1}, "source"),
    ({"source": "A", "signal": "x"}, "value"),
    ({"source": "A", "signal": "x", "value": [1, 2]}, "value must be"),
    ({"source": "A", "values": {}}, "values"),
    ({"source": "A", "signal": "x", "value": 1, "context": {"a": {"b": 1}}}, "context"),
])
def test_validation(api, body, msg):
    r = api.post("/api/v1/signals", json=body, headers=H)
    assert r.status_code == 400 and msg in r.json["error"]


def test_non_json_body(api):
    r = api.post("/api/v1/signals", data="x", headers=H)
    assert r.status_code == 400


def test_notify(api, mgr, channel):
    r = api.post("/api/v1/notify", headers=H, json={
        "source": "HAAS_TM1P", "signal": "door", "message": "opened during cycle",
        "severity": "critical", "group": "haas"})
    assert r.status_code == 202 and r.json["alerts"][0]["severity"] == "CRITICAL"
    mgr.outbox.process_due()
    assert "opened during cycle" in channel.sent[0].text
    r = api.post("/api/v1/notify", headers=H, json={"source": "A", "signal": "b", "message": "m",
                                                    "severity": "panic"})
    assert r.status_code == 400


def test_rules_and_recipients(api):
    rules = api.get("/api/v1/rules", headers=H).json["rules"]
    assert rules[0]["type"] == "threshold" and rules[0]["limit"] == 4000
    rec = api.get("/api/v1/recipients", headers=H).json["recipients"]
    assert rec[0]["phone"].startswith("+*") and rec[0]["min_severity"] == Severity.INFO.name


def test_cors(api):
    r = api.get("/api/v1/health", headers={"Origin": "http://dash.local"})
    assert r.headers["Access-Control-Allow-Origin"] == "http://dash.local"
    r = api.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers
    r = api.options("/api/v1/signals", headers={"Origin": "http://dash.local"})
    assert r.status_code == 200


class _FlaskSession:
    """Adapts Flask's test client to the requests.Session interface used by Com2WAClient."""
    def __init__(self, tc):
        self.tc, self.headers = tc, {}

    def request(self, method, url, timeout=None, json=None, params=None):
        path = url.split("http://test", 1)[1]
        r = self.tc.open(path, method=method, json=json, query_string=params, headers=self.headers)
        return _Resp(r)


class _Resp:
    def __init__(self, r):
        self.status_code, self.ok, self._data, self.text = r.status_code, r.status_code < 400, r.json, r.text

    def json(self):
        return self._data


def test_python_client(api):
    wa = Com2WAClient("http://test", api_key=KEY, session=_FlaskSession(api))
    assert wa.health()["status"] == "ok"
    assert wa.signal("HAAS_TM1P", "spindle_speed", 4600)[0]["kind"] == "alert"
    assert wa.active_alerts()[0]["value"] == 4600
    assert wa.signals("UF850", {"warn_code": 0}) == []
    with pytest.raises(Com2WAError) as e:
        wa.notify("A", "b", "m", severity="nope")
    assert e.value.status == 400

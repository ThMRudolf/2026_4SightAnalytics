# Com2WA REST API

The REST API makes the Com2WA alert engine available to any program that can send HTTP,
not just Python code that imports `AlertManager`. That includes other dashboards, Node.js or
C# tools, PLC/edge gateways, Node-RED, cron scripts and PowerShell.

```
 Haas backend ─┐
 UFactory  ────┤  HTTP + JSON        ┌──────────────── com2wa-server ───────────────┐
 Node-RED  ────┼──────────────────▶  │ REST API → AlertManager → outbox → WhatsApp  │
 PowerShell ───┘  X-API-Key header   └───────────────────────────────────────────────┘
```

Base URL: `http://<host>:8090/api/v1`

---

## 1. Start the server

```bash
pip install -e "Com2WA[server]"                  # Flask + waitress (production WSGI, Windows-friendly)
cp Com2WA/config.example.yaml Com2WA/config.yaml
cp Com2WA/.env.example        Com2WA/.env        # set COM2WA_API_KEY (+ WhatsApp credentials)

com2wa-server --config Com2WA/config.yaml        # → http://127.0.0.1:8090/api/v1
# options: --host 0.0.0.0 --port 8090 --dev
```

Settings in `config.yaml`:

```yaml
api:
  host: 127.0.0.1        # use 0.0.0.0 to accept other machines on the LAN
  port: 8090
  cors_origins: []       # browser origins allowed to call the API, e.g. ["http://localhost:8888"]
```

**Security**
- When `COM2WA_API_KEY` is set, every endpoint except `/health` requires the header `X-API-Key: <key>`.
- Without a key, the server logs a warning. Only run it that way on `127.0.0.1`.
- Generate a key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Phone numbers are always masked in API responses (`+*********4567`).
- Don't put the key in browser JavaScript on a page that untrusted people can open. Send signals
  from the dashboard **backend**, or put a reverse proxy in front of the API.

---

## 2. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | Liveness and summary. Needs no auth |
| POST | `/signals` | Send signal values. The rules decide whether an alert is sent |
| POST | `/notify` | Send a one-off message that bypasses the rules |
| GET  | `/alerts/active` | List the alerts that are currently active |
| GET  | `/messages?status=&limit=` | Show the outbox / delivery log (newest first, `limit` ≤ 500) |
| GET  | `/rules` | List the loaded rules |
| GET  | `/recipients` | List recipients (phone numbers masked) |

### POST `/signals`

Send values on **every poll cycle**. Only state changes produce WhatsApp messages,
because debounce, hysteresis and cooldown are applied on the server. Three body forms are accepted:

```jsonc
// a) single signal
{"source": "HAAS_TM1P", "signal": "spindle_speed", "value": 4520, "context": {"prog": "O01234"}}

// b) batch: several signals of one machine (recommended per poll cycle)
{"source": "UF850", "values": {"servo_temp_j3": 57.5, "warn_code": 0}, "context": {"cell": "A"}}

// c) list of a) and/or b)
[{"source": "HAAS_TM1P", "signal": "spindle_speed", "value": 4520},
 {"source": "UF850", "values": {"warn_code": 3}}]
```

- `value` is a number, string, boolean or `null`.
- `context` is optional. It must be an object with scalar values, and those values are appended to the message.
- One request can carry at most 1000 signals.

Response `200`. `alerts` lists the messages this call triggered and is usually empty:

```json
{"accepted": 1,
 "alerts": [{"kind": "alert", "source": "HAAS_TM1P", "signal": "spindle_speed", "severity": "INFO",
             "group": "haas", "value": 4520, "limit": "> 4000 rpm", "unit": "rpm",
             "context": {"prog": "O01234"}, "timestamp": 1791134584.49}]}
```

`kind` is one of `alert`, `resolved`, `reminder` or `suppressed_summary`.

### POST `/notify`

```json
{"source": "HAAS_TM1P", "signal": "door", "message": "opened during cycle",
 "severity": "CRITICAL", "group": "haas"}
```

- `severity` is `INFO`, `WARNING` (the default) or `CRITICAL`.
- `group` defaults to `*`, which means all recipients.
- The response is `202` with the same `alerts` list as `/signals`.

### GET `/alerts/active`

```json
{"alerts": [{"source": "HAAS_TM1P", "signal": "spindle_speed", "value": 4520,
             "since": 1791134584.49, "severity": "INFO"}]}
```

### GET `/messages?status=sent&limit=3`

```json
{"messages": [{"id": 3, "created": 1791134584.68, "to_phone": "+*********0000",
               "alert_key": "HAAS_TM1P/door", "kind": "alert", "status": "sent", "attempts": 1,
               "last_error": null, "provider_id": "wamid.HBgM...", "sent_at": 1791134584.69}]}
```

`status` is one of `pending`, `sent` or `failed`.

### Errors

All errors are JSON: `{"error": "<reason>"}`.

| Code | When |
|---|---|
| `400` | The request is invalid, e.g. `'source' must be a non-empty string` or `value must be a number, string, boolean or null` |
| `401` | `X-API-Key` is missing or wrong |
| `404` / `405` | The path or method is unknown |
| `413` | The request has more than 1000 signals |

---

## 3. Example: spindle-speed demo alert (> 4000 rpm)

This example walks through the whole flow with curl. With the example config, the alert fires
after 2 s above 4000 rpm and clears below 3800 rpm.

```bash
API=http://127.0.0.1:8090/api/v1
KEY="X-API-Key: <your-key>"

curl -s $API/health
# {"status":"ok","version":"0.1.0","channel":"meta_cloud","rules":6,"recipients":2,"worker_alive":true,"auth":true}

# spindle goes to 4520 rpm — send it every poll cycle
curl -s -X POST $API/signals -H "$KEY" -H "Content-Type: application/json" \
     -d '{"source":"HAAS_TM1P","signal":"spindle_speed","value":4520,"context":{"prog":"O01234"}}'
# after the debounce time: {"accepted":1,"alerts":[{"kind":"alert", ...}]}  → WhatsApp sent

curl -s $API/alerts/active -H "$KEY"

# spindle back to 3000 rpm → {"alerts":[{"kind":"resolved", ...}]}  → ✅ WhatsApp sent
curl -s -X POST $API/signals -H "$KEY" -H "Content-Type: application/json" \
     -d '{"source":"HAAS_TM1P","signal":"spindle_speed","value":3000}'

curl -s "$API/messages?limit=5" -H "$KEY"     # delivery status
```

The recipient receives the following message (Spanish recipient shown):

```
⚠️ Alerta INFO en HAAS_TM1P
Señal: Velocidad del husillo
Valor: 4520 rpm (límite > 4000 rpm) | prog=O01234
Hora: 2026-10-04 10:15:02

Enviado por el monitoreo de procesos Com2WA.
```

> If a second excursion within `cooldown_s` (60 s for the demo rule) returns `"alerts": []`,
> that is expected. The cooldown stops flapping signals from flooding phones.

---

## 4. Client examples

### PowerShell (Windows)

```powershell
$api = "http://127.0.0.1:8090/api/v1"
$h   = @{ "X-API-Key" = $env:COM2WA_API_KEY }

$body = @{ source = "HAAS_TM1P"; signal = "spindle_speed"; value = 4520 } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri "$api/signals" -Headers $h -ContentType "application/json" -Body $body
$r.alerts | Format-Table kind, source, signal, value

(Invoke-RestMethod -Uri "$api/rules" -Headers $h).rules | Format-Table source, signal, type, op, limit
```

### Python: client included in the package

```python
from com2wa.client import Com2WAClient, Com2WAError

wa = Com2WAClient("http://127.0.0.1:8090", api_key="<your-key>", timeout_s=3)

wa.signal("HAAS_TM1P", "spindle_speed", 4520, context={"prog": "O01234"})
wa.signals("UF850", {"servo_temp_j3": 57.5, "warn_code": 0})       # batch
wa.notify("HAAS_TM1P", "door", "opened during cycle", severity="CRITICAL", group="haas")

print(wa.active_alerts())
print(wa.messages(status="failed"))

try:
    wa.notify("X", "y", "z", severity="PANIC")
except Com2WAError as e:          # e.status == 400
    print(e)
```

A runnable version is in `examples/api_client_demo.py`.

### Python: dashboard backend via HTTP (alert engine runs as a separate service)

Use this form instead of the in-process adapter when several backends should share **one**
alert service. Add it in `IIoT_Haas/Backend/main.py` → `poller_loop()`:

```python
from com2wa.adapters import haas_signals
from com2wa.client import Com2WAClient, Com2WAError

WA = Com2WAClient(os.environ.get("COM2WA_URL", "http://127.0.0.1:8090"),
                  api_key=os.environ.get("COM2WA_API_KEY"), timeout_s=1)
...
rpm = rpm_sample or current_data.get("spindle_rpm")
save_sample(current_data, axes_data, spindle_rpm_override=rpm)
try:
    WA.signals("HAAS_TM1P", haas_signals(current_data, rpm))
except (Com2WAError, OSError) as e:          # alerting must never break data acquisition
    log.warning("Com2WA unavailable: %s", e)
```

### JavaScript (Node.js ≥ 18, or a browser when `cors_origins` allows it)

```js
const API = "http://127.0.0.1:8090/api/v1";
const headers = { "Content-Type": "application/json", "X-API-Key": process.env.COM2WA_API_KEY };

const res = await fetch(`${API}/signals`, {
  method: "POST", headers,
  body: JSON.stringify({ source: "HAAS_TM1P", signal: "spindle_speed", value: 4520 }),
});
const { accepted, alerts } = await res.json();
if (!res.ok) throw new Error(`Com2WA ${res.status}`);
alerts.forEach(a => console.log(a.kind, a.source, a.signal, a.value));
```

### Node-RED / PLC gateways

Use an **http request** node with method `POST`, URL `…/api/v1/signals` and headers
`Content-Type: application/json` and `X-API-Key`. Set the payload to
`{"source":"<machine>","values":{...}}`.

---

## 5. Library or API: which to use?

| | In-process (`AlertManager`) | REST API (`com2wa-server`) |
|---|---|---|
| Caller language | Python only | Any language that speaks HTTP |
| Latency / dependency | No network hop, nothing extra to run | One local HTTP call. The server must be running |
| Several machines/backends | Each runs its own engine and outbox | One central engine, one outbox, one audit log |
| Best for | A single Python dashboard backend | A production cell with several sources |

Both use the same rules, config file and WhatsApp channel, so you can switch later without
changing the configuration.

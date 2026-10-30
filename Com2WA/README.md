# Com2WA – WhatsApp alerts for process monitoring

A reusable Python package that turns machine signals (spindle speed, temperatures,
error codes, tool wear, …) into **deduplicated WhatsApp alerts** sent through the
official **Meta WhatsApp Cloud API**. It doesn't depend on any particular dashboard: anything
that can call a Python function can use it.

```
poll loop ──update(source, signal, value)──▶ AlertManager
HTTP client ──POST /api/v1/signals ─────────▶    (same engine via com2wa-server) ──▶ SQLite outbox ──worker──▶ WhatsApp
             (thread-safe, no network I/O)    rules, debounce,   (persist, retry,
                                              hysteresis,         backoff, audit)
                                              cooldown, routing
```

## Install
```bash
pip install -e Com2WA            # from the repo root; add [dotenv] to read .env files
```
Requires Python ≥ 3.10.

## Quick start
```python
from com2wa import AlertManager
ALERTS = AlertManager.from_config("Com2WA/config.yaml")   # starts the delivery worker

# inside any poll loop (1–10 Hz is fine — only state *changes* produce messages)
ALERTS.update("HAAS_TM1P", "spindle_speed", 4520)
ALERTS.update("UF850", "servo_temp_j3", 57.1)

ALERTS.notify("HAAS_TM1P", "door", "opened during cycle", severity="CRITICAL")  # ad hoc
```

### …or over HTTP from any language (REST API)
```bash
pip install -e "Com2WA[server]"
com2wa-server --config Com2WA/config.yaml          # http://127.0.0.1:8090/api/v1

curl -X POST http://127.0.0.1:8090/api/v1/signals -H "X-API-Key: $COM2WA_API_KEY"      -H "Content-Type: application/json"      -d '{"source":"HAAS_TM1P","signal":"spindle_speed","value":4520}'
```
Endpoints: `/health`, `/signals`, `/notify`, `/alerts/active`, `/messages`, `/rules`, `/recipients`.
A Python client (`com2wa.client.Com2WAClient`) is included. **See [docs/API.md](docs/API.md)** for the full reference
and examples in curl, PowerShell, Python, JavaScript and Node-RED.

Try it without WhatsApp (console channel, simulated clock):
```bash
python Com2WA/examples/simulate.py
```

## How alerts behave
Each `(rule, source, signal)` pair runs a small state machine:
`OK → PENDING → ACTIVE → CLEARING → OK`

| Setting | Effect |
|---|---|
| `debounce_s` | Value must stay violated (or OK) this long before an alert (or "resolved") fires |
| `hysteresis` | Once active, the value must drop back past `limit ∓ hysteresis` to clear |
| `cooldown_s` | Minimum time between two alerts for the same signal (anti-flapping) |
| `notify_on_clear` | Send a ✅ "resolved" message |
| `repeat_every_s` | Reminder while the alert stays active |
| `rate_limit_per_min` | Global cap. Excess messages are dropped and summarised later |

Rule types: `threshold` (`>`, `>=`, `<`, `<=`), `nonzero` (error/warn codes, alarm text),
`equals` (states such as `STOPPED`, with `negate: true` available). `source` and `signal` accept
glob patterns (`servo_temp_j*`). In a `label`, `{idx}` is replaced with the trailing number of the signal name.

Recipients are routed by `groups`, `min_severity` and optional `quiet_hours`
(CRITICAL alerts ignore quiet hours). Each recipient gets messages in their language (`de`, `es` or `en`).

## Configuration
- `config.example.yaml`: copy to `config.yaml`. It holds rules, recipients and timing.
- `.env.example`: copy to `.env`. It holds `WA_TOKEN`, `WA_PHONE_NUMBER_ID` and `WA_API_VERSION`.
- Both `config.yaml` and `.env` are git-ignored.

## Meta WhatsApp Cloud API setup
1. Create a Meta Business account and an app at developers.facebook.com, then add the **WhatsApp** product.
2. In **WhatsApp → API Setup**, note the **Phone number ID**. For production, register your own
   number. The free test number can only send to up to 5 verified recipients.
3. Create a **System User** in Business Settings with the `whatsapp_business_messaging`
   permission and generate a **permanent token**. Put it in `.env` as `WA_TOKEN`.
4. Check the current Graph API version in the dashboard and set `WA_API_VERSION`.
5. Create the two message templates below (category **Utility**) in **WhatsApp Manager →
   Message templates**, once for each language (de, es, en), and wait for approval.
6. Run a live test: `python Com2WA/examples/send_test.py +52155XXXXXXXX --lang es`
7. Set `channel: meta_cloud` in `config.yaml`.

### Templates to submit
Approval requires one sample value per variable, e.g. `WARNING`, `HAAS_TM1P`, `Spindle speed`,
`4500 rpm (limit > 4000 rpm)`, `2026-10-04 10:00:00`.

**`machine_alert`**: 5 variables (severity, machine, signal, value, time)

| lang | body |
|---|---|
| en | `⚠️ {{1}} alert on {{2}}`<br>`Signal: {{3}}`<br>`Value: {{4}}`<br>`Time: {{5}}`<br><br>`Sent by Com2WA process monitoring.` |
| de | `⚠️ {{1}}-Alarm an {{2}}`<br>`Signal: {{3}}`<br>`Wert: {{4}}`<br>`Zeit: {{5}}`<br><br>`Gesendet von Com2WA Prozessüberwachung.` |
| es | `⚠️ Alerta {{1}} en {{2}}`<br>`Señal: {{3}}`<br>`Valor: {{4}}`<br>`Hora: {{5}}`<br><br>`Enviado por el monitoreo de procesos Com2WA.` |

**`machine_alert_resolved`**: 4 variables (machine, signal, value, time)

| lang | body |
|---|---|
| en | `✅ Resolved on {{1}}`<br>`Signal: {{2}}`<br>`Value: {{3}}`<br>`Time: {{4}}`<br><br>`Sent by Com2WA process monitoring.` |
| de | `✅ Behoben an {{1}}`<br>`Signal: {{2}}`<br>`Wert: {{3}}`<br>`Zeit: {{4}}`<br><br>`Gesendet von Com2WA Prozessüberwachung.` |
| es | `✅ Resuelto en {{1}}`<br>`Señal: {{2}}`<br>`Valor: {{3}}`<br>`Hora: {{4}}`<br><br>`Enviado por el monitoreo de procesos Com2WA.` |

The texts must match `src/com2wa/i18n.py` (`BODIES`). If you registered a different
language code such as `en_US`, map it with `template_lang_codes` in the config.

## Dashboard integration
Ready-made adapters are in `com2wa.adapters`.

**Haas TM-1P** (`IIoT_Haas/Backend/main.py`, `poller_loop()`):
```python
from com2wa import AlertManager
from com2wa.adapters import feed_haas
ALERTS = AlertManager.from_config("/path/to/Com2WA/config.yaml")
...
current_data          = parse_current(root_c)
axes_data, rpm_sample = parse_samples(root_s)
rpm = rpm_sample or current_data.get("spindle_rpm")
save_sample(current_data, axes_data, spindle_rpm_override=rpm)
feed_haas(ALERTS, current_data, rpm=rpm)            # optional: tool_lib=[{"t":4,"lWear":..,"dWear":..}]
```
It emits these signals: `spindle_speed`, `active_alarms`, `run_status`, `controller_mode`,
`tool_wear_len_t<N>` and `tool_wear_dia_t<N>`.

**UFactory 850** (`IIoT_UFactory/backend/backend.py`, `_poll_loop()`):
```python
from com2wa.adapters import feed_ufactory
...
with _state_lock:
    _cached_state.update(update)
feed_ufactory(ALERTS, update)
```
It emits these signals: `connected`, `error_code`, `warn_code`, `motion_state`, `servo_temp_j1..6`
and `joint_usage_j1..6` (%, mapped the same way as the dashboard bar).

Signals without a matching rule are ignored, so you can add rules (alarms, connectivity, stop
states, …) later with config changes only.

## Adding another provider
Subclass `com2wa.channels.Channel` and implement `send(msg) -> DeliveryResult`. Return
`retryable=True` for transient errors. Pass an instance with
`AlertManager.from_config(path, channel=MyChannel())`.

## Tests
```bash
pip install -e "Com2WA[dev]" && pytest Com2WA
```

## Outbox / audit
Every message is written to `com2wa_outbox.db` before it is sent:
```sql
SELECT id, datetime(created,'unixepoch','localtime'), to_phone, alert_key, kind, status, attempts, last_error
FROM outbox ORDER BY id DESC LIMIT 20;
```
Pending rows are re-sent automatically after a restart.

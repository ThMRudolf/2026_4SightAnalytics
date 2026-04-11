# CNC Milling Process Dashboard

Real-time monitoring dashboard for a **Haas TM-1P** (or any MTConnect-compatible machine).

```
dashboard/
├── index.html        Frontend — Bootstrap 5 dashboard
├── styles.css        Custom styles
├── app.js            Frontend logic (MTConnect + REST API consumer)
├── backend.py        Python backend — collector + SQLite + REST API
├── requirements.txt  Python dependencies
└── milling.db        SQLite database (auto-created on first run)
```

---

## Quick Start

### 1 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2 — Start the backend

```bash
python backend.py --host 192.168.1.100 --port 5000
```

| Argument | Default | Description |
|---|---|---|
| `--host` | `192.168.1.100` | MTConnect agent IP |
| `--port` | `5000` | MTConnect agent port |
| `--interval` | `1.0` | Poll interval in seconds |
| `--db` | `milling.db` | SQLite database file path |
| `--api-port` | `8080` | Flask REST API listen port |
| `--debug` | off | Verbose logging |

### 3 — Serve the frontend

```bash
# Python simple server (development)
python -m http.server 3000

# Or point any web server (nginx, Apache) at the dashboard/ folder
```

### 4 — Open the dashboard

Navigate to `http://localhost:3000` and enter your connection details in the ⚙ modal:

| Field | Example |
|---|---|
| Device IP | `192.168.1.100` |
| Port | `5000` |
| Python Backend URL | `http://localhost:8080` |

When **Backend URL** is set, the frontend reads from the REST API (with full DB history). Without it, the browser polls MTConnect directly.

---

## REST API Reference

All endpoints are served by the Python backend on port `8080`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | Health check, row counts |
| `/api/latest` | GET | Latest snapshot (all data joined) |
| `/api/trajectory?limit=200` | GET | XYZ positions for trajectory plots |
| `/api/history/spindle?limit=300` | GET | Spindle RPM time series |
| `/api/history/axes?axis=X&limit=300` | GET | Axis position + velocity history |
| `/api/history/cycle_time?limit=300` | GET | Cycle time / runtime history |
| `/api/history/tool?limit=100` | GET | Tool change log |
| `/api/history/sensor?limit=300` | GET | Current sensor readings |
| `/api/stats` | GET | Aggregate statistics |
| `/api/sensor` | POST | Ingest current sensor reading (JSON) |

### POST `/api/sensor` — sensor payload

```json
{
  "spindle_A": 12.4,
  "x_axis_A":  3.1,
  "y_axis_A":  2.7,
  "timestamp": "2024-01-01T12:00:00Z"
}
```

---

## Database Schema

```
machine_samples    — one row per poll cycle (RPM, overrides, timers, alarms, program)
axis_positions     — one row per axis per cycle (position_mm, velocity_mm_s)
tool_state         — tool number, station, power per cycle
current_sensor     — future current sensor readings (A, W, load %)
```

All tables are indexed on `ts` (ISO-8601 UTC) for fast time-range queries.
WAL journal mode is enabled for concurrent read/write access.

---

## Architecture

```
[Haas TM-1P]
     │ MTConnect (HTTP/XML)
     ▼
[backend.py]  ──────────────────────────────── polls /current + /sample every 1s
     │                                          parses XML, derives velocities
     │                                          writes to SQLite (WAL mode)
     │ Flask REST API (:8080)
     ▼
[index.html / app.js]  ────────────────────── polls /api/latest every 1s
     │                                          renders tachometers, trajectory,
     │                                          tool info, timers, G-codes
     ▼
[Browser]

Optional future path:
[Current Sensor HW]  →  POST /api/sensor  →  current_sensor table  →  sparkline charts
```

---

## CORS

The backend sends `Access-Control-Allow-Origin: *` by default.
For production, set `CFG["cors_origin"]` in `backend.py` to your frontend's exact origin.

# =============================================================================
# backend/main.py — CNC Milling Dashboard: Python Backend (Docker Container)
# =============================================================================
# Responsibilities:
#   1. Receive connection config from the Streamlit frontend via POST /api/connect
#   2. Poll the MTConnect agent (/current, /sample) in a background thread
#   3. Parse XML, derive velocities, compute sensor metrics
#   4. Persist every sample to SQLite (WAL mode, 4 indexed tables)
#   5. Serve all analytical results as JSON via REST API
#   6. Accept current sensor data via POST /api/sensor (future integration)
#
# Communication contract with frontend:
#   Frontend  →  POST /api/connect          { mtc_host, mtc_port, interval }
#   Backend   →  GET  /api/latest           full snapshot JSON
#   Backend   →  GET  /api/trajectory       list of {x,y,z,ts}
#   Backend   →  GET  /api/history/spindle  RPM time series
#   Backend   →  GET  /api/history/axes     axis position + velocity series
#   Backend   →  GET  /api/stats            aggregate statistics
#   Frontend  →  POST /api/sensor           { spindle_A, x_axis_A, y_axis_A }
# =============================================================================

import logging           # Console logging
import sqlite3           # Built-in SQLite interface
import threading         # Background poller thread
import time              # time.sleep(), time.monotonic()
import xml.etree.ElementTree as ET   # MTConnect XML parser

from contextlib import contextmanager        # Enables "with get_db() as conn:" pattern
from datetime import datetime, timezone      # UTC timestamps
from pathlib import Path                     # Cross-platform file paths

import requests                              # HTTP requests to MTConnect agent
from flask import Flask, jsonify, request    # Web framework for REST API
from flask_cors import CORS                  # Cross-Origin Resource Sharing for Streamlit container

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mill-backend")

# ---------------------------------------------------------------------------
# Flask app + CORS
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)   # Allow all origins — Streamlit container has a different origin than this container

# ---------------------------------------------------------------------------
# Runtime configuration — mutated by POST /api/connect
# ---------------------------------------------------------------------------
CFG = {
    "mtc_host":      None,    # MTConnect agent IP (set by frontend at runtime)
    "mtc_port":      5000,    # MTConnect agent port
    "db_path":       "/data/milling.db",   # Mounted Docker volume path
    "poll_interval": 1.0,     # Seconds between polls
    "connected":     False,   # Whether the poller is currently running
}

# ---------------------------------------------------------------------------
# Database schema — 4 tables + 4 indices
# ---------------------------------------------------------------------------
DB_SCHEMA = """
-- One row per poll cycle: machine state snapshot
CREATE TABLE IF NOT EXISTS machine_samples (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT    NOT NULL,       -- ISO-8601 UTC timestamp
    spindle_rpm       REAL,                   -- Spindle speed in RPM
    spindle_override  REAL,                   -- Spindle override %
    feed_override     REAL,                   -- Feed override %
    rapid_override    REAL,                   -- Rapid override %
    cycle_time_s      INTEGER,                -- Current cycle time (s)
    spindle_time_s    INTEGER,                -- Total spindle on-time (s)
    machine_runtime_s INTEGER,                -- Total machine runtime (s)
    m30_counter       INTEGER,                -- M30 program-end counter
    run_status        TEXT,                   -- ACTIVE / STOPPED / IDLE
    controller_mode   TEXT,                   -- AUTO / MANUAL / MDI
    active_alarms     TEXT,                   -- Alarm text or "NO ACTIVE ALARMS"
    active_program    TEXT,                   -- NC program filename
    active_gcodes     TEXT                    -- Comma-separated active G-codes
);

-- One row per axis per poll cycle: positions and derived velocities
CREATE TABLE IF NOT EXISTS axis_positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id     INTEGER NOT NULL REFERENCES machine_samples(id),
    ts            TEXT    NOT NULL,
    axis          TEXT    NOT NULL,           -- 'X', 'Y', 'Z', 'C', 'A', 'B'
    position_mm   REAL    NOT NULL,           -- Actual position in mm
    velocity_mm_s REAL                        -- Derived: |Δpos| / Δt
);

-- Tool state per cycle: number, station, power
CREATE TABLE IF NOT EXISTS tool_state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id    INTEGER NOT NULL REFERENCES machine_samples(id),
    ts           TEXT    NOT NULL,
    tool_number  INTEGER,
    station      INTEGER,
    spindle_num  INTEGER,
    max_power_kw REAL
);

-- Future current sensor readings
CREATE TABLE IF NOT EXISTS current_sensor (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    spindle_a    REAL,                        -- Spindle current (A)
    x_axis_a     REAL,                        -- X-axis drive current (A)
    y_axis_a     REAL,                        -- Y-axis drive current (A)
    power_est_w  REAL,                        -- Estimated power: √3 × 480V × I
    load_pct     REAL,                        -- Load %: I / I_rated × 100
    source       TEXT DEFAULT 'external'
);

-- Indices for fast time-range queries
CREATE INDEX IF NOT EXISTS idx_samples_ts   ON machine_samples(ts);
CREATE INDEX IF NOT EXISTS idx_axis_ts      ON axis_positions(ts);
CREATE INDEX IF NOT EXISTS idx_axis_sample  ON axis_positions(sample_id);
CREATE INDEX IF NOT EXISTS idx_current_ts   ON current_sensor(ts);
"""

# ---------------------------------------------------------------------------
# Database connection — thread-safe context manager
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    """Open a SQLite connection with WAL mode and FK support; auto-commit or rollback."""
    Path(CFG["db_path"]).parent.mkdir(parents=True, exist_ok=True)  # Ensure /data/ exists
    conn = sqlite3.connect(CFG["db_path"], check_same_thread=False)
    conn.row_factory = sqlite3.Row                # Rows accessible as dicts
    conn.execute("PRAGMA journal_mode=WAL")       # WAL: concurrent reads + writes
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables and indices if they don't exist yet."""
    with get_db() as conn:
        conn.executescript(DB_SCHEMA)
    log.info("Database ready: %s", CFG["db_path"])

# ---------------------------------------------------------------------------
# MTConnect XML parsing helpers
# ---------------------------------------------------------------------------

NS = "urn:mtconnect.org:MTConnectStreams:1.2"   # MTConnect namespace URI


def _tag(name):
    """Return namespace-qualified tag name for ElementTree."""
    return f"{{{NS}}}{name}"


def xml_all(root, tag, name_attr=None):
    """Return all elements matching tag + optional name attribute."""
    result = []
    for el in root.iter(_tag(tag)):
        if name_attr is None or el.get("name") == name_attr:
            result.append(el)
    return result


def xml_get(root, tag, name_attr=None):
    """Return the first matching element, or None."""
    els = xml_all(root, tag, name_attr)
    return els[0] if els else None


def xml_val(root, tag, name_attr=None, default=None):
    """Return the stripped text value of the first matching element."""
    el = xml_get(root, tag, name_attr)
    return el.text.strip() if el is not None and el.text else default

# ---------------------------------------------------------------------------
# Previous axis positions — used for velocity derivation between poll cycles
# ---------------------------------------------------------------------------
_prev_positions = {}   # { axis_name: (position_mm, timestamp_str) }


def parse_current(root):
    """
    Extract machine-state snapshot from /current XML.
    Returns a flat dictionary ready for DB insertion.
    """
    data = {}

    # Spindle RPM
    val = xml_val(root, "SpindleSpeed", "SpindleSpeed")
    data["spindle_rpm"] = float(val) if val else None

    # Override values (%)
    val = xml_val(root, "SpindleSpeed", "SpindleSpeedOverride")
    data["spindle_override"] = float(val) if val else None
    val = xml_val(root, "PathFeedrate", "FeedrateOverride")
    data["feed_override"] = float(val) if val else None
    val = xml_val(root, "AxisFeedrate", "RapidOverride")
    data["rapid_override"] = float(val) if val else None

    # Timers (seconds)
    val = xml_val(root, "Message", "MachineRunTime")
    data["machine_runtime_s"] = int(val) if val else None
    val = xml_val(root, "Message", "SpindleTime")
    data["spindle_time_s"] = int(val) if val else None

    # Cycle time — take last (most recent) entry
    els = xml_all(root, "AccumulatedTime", "ThisCycle")
    data["cycle_time_s"] = int(els[-1].text.strip()) if els else None

    # Program / execution state
    data["active_program"]  = xml_val(root, "Program", "Program")
    data["run_status"]      = xml_val(root, "Execution", "RunStatus")
    data["controller_mode"] = xml_val(root, "ControllerMode", "Mode")
    data["active_alarms"]   = xml_val(root, "Message", "ActiveAlarms")

    val = xml_val(root, "Message", "Gcodes")
    data["active_gcodes"] = val or None

    val = xml_val(root, "Message", "M30Counter1")
    data["m30_counter"] = int(val) if val else None

    # Tool info
    tool = {}
    val = xml_val(root, "Message", "DHMT_Codes")
    if val:
        parts = val.split(",")
        tool["tool_number"] = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else None
    tool["station"]     = int(xml_val(root, "Message", "STATION")       or 0) or None
    tool["spindle_num"] = int(xml_val(root, "Message", "SpindleNumber") or 0) or None
    val = xml_val(root, "Message", "SpindleMaxPower")
    tool["max_power_kw"] = float(val) if val else None
    data["tool"] = tool

    return data


def parse_samples(root):
    """
    Extract axis position time-series from /sample XML.
    Derives velocity as |Δpos| / Δt using the stored previous position.
    Returns (axes_data dict, latest_spindle_rpm).
    """
    global _prev_positions
    axes_data = {}

    for axis_name in ("X", "Y", "Z", "C", "A", "B"):
        tag_name = f"{axis_name}_Axis_Actual_Position"
        els = xml_all(root, "PathPosition", tag_name)
        if not els:
            continue

        last_el  = els[-1]
        pos_val  = last_el.text.strip() if last_el.text else None
        ts_str   = last_el.get("timestamp", "")

        if pos_val is None:
            continue

        position = float(pos_val)
        velocity = None

        # Derive velocity from stored previous sample
        prev = _prev_positions.get(axis_name)
        if prev:
            prev_pos, prev_ts = prev
            try:
                t1 = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(ts_str.replace("Z",  "+00:00"))
                dt = (t2 - t1).total_seconds()
                if dt > 0:
                    velocity = abs(position - prev_pos) / dt    # mm/s
            except Exception:
                pass

        _prev_positions[axis_name] = (position, ts_str)
        axes_data[axis_name] = {
            "position_mm":   position,
            "velocity_mm_s": velocity,
            "ts":            ts_str,
        }

    rpm_els     = xml_all(root, "SpindleSpeed", "SpindleSpeed")
    spindle_rpm = float(rpm_els[-1].text.strip()) if rpm_els else None

    return axes_data, spindle_rpm

# ---------------------------------------------------------------------------
# Database write operations
# ---------------------------------------------------------------------------

def save_sample(current_data, axes_data, spindle_rpm_override=None):
    """
    Persist one complete poll cycle across all three tables in one transaction.
    Returns the new sample_id.
    """
    ts          = datetime.now(timezone.utc).isoformat()
    spindle_rpm = spindle_rpm_override or current_data.get("spindle_rpm")

    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO machine_samples
                (ts, spindle_rpm, spindle_override, feed_override, rapid_override,
                 cycle_time_s, spindle_time_s, machine_runtime_s, m30_counter,
                 run_status, controller_mode, active_alarms, active_program, active_gcodes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ts,
            spindle_rpm,
            current_data.get("spindle_override"),
            current_data.get("feed_override"),
            current_data.get("rapid_override"),
            current_data.get("cycle_time_s"),
            current_data.get("spindle_time_s"),
            current_data.get("machine_runtime_s"),
            current_data.get("m30_counter"),
            current_data.get("run_status"),
            current_data.get("controller_mode"),
            current_data.get("active_alarms"),
            current_data.get("active_program"),
            current_data.get("active_gcodes"),
        ))
        sample_id = cur.lastrowid

        for axis, info in axes_data.items():
            conn.execute("""
                INSERT INTO axis_positions (sample_id, ts, axis, position_mm, velocity_mm_s)
                VALUES (?,?,?,?,?)
            """, (sample_id, info.get("ts", ts), axis,
                  info["position_mm"], info.get("velocity_mm_s")))

        tool = current_data.get("tool", {})
        if any(v is not None for v in tool.values()):
            conn.execute("""
                INSERT INTO tool_state (sample_id, ts, tool_number, station, spindle_num, max_power_kw)
                VALUES (?,?,?,?,?,?)
            """, (sample_id, ts,
                  tool.get("tool_number"), tool.get("station"),
                  tool.get("spindle_num"), tool.get("max_power_kw")))

    return sample_id


def save_current_sensor(spindle_a, x_a, y_a, source="external"):
    """Persist one current-sensor reading with derived power and load."""
    ts        = datetime.now(timezone.utc).isoformat()
    power_est = round(1.732 * 480 * spindle_a, 1) if spindle_a is not None else None
    load_pct  = min(100, round(spindle_a / 30 * 100, 1)) if spindle_a is not None else None
    with get_db() as conn:
        conn.execute("""
            INSERT INTO current_sensor (ts, spindle_a, x_axis_a, y_axis_a, power_est_w, load_pct, source)
            VALUES (?,?,?,?,?,?,?)
        """, (ts, spindle_a, x_a, y_a, power_est, load_pct, source))

# ---------------------------------------------------------------------------
# MTConnect background poller
# ---------------------------------------------------------------------------
_poller_thread  = None    # Thread object
_poller_active  = False   # Loop control flag


def build_url(path):
    """Construct full MTConnect URL from config."""
    return f"http://{CFG['mtc_host']}:{CFG['mtc_port']}{path}"


def fetch_xml(path, timeout=3):
    """GET an MTConnect endpoint and return the parsed XML root, or None on error."""
    try:
        r = requests.get(build_url(path), timeout=timeout)
        r.raise_for_status()
        return ET.fromstring(r.content)
    except requests.RequestException as e:
        log.warning("fetch_xml %s — %s", path, e)
        return None
    except ET.ParseError as e:
        log.warning("XML parse error %s — %s", path, e)
        return None


def poller_loop():
    """
    Background thread: poll /current and /sample every CFG['poll_interval'] seconds,
    parse both XML responses, derive velocities, and persist to SQLite.
    """
    global _poller_active
    log.info("Poller started → %s:%s (interval %.1fs)",
             CFG["mtc_host"], CFG["mtc_port"], CFG["poll_interval"])

    while _poller_active:
        t0 = time.monotonic()
        try:
            root_c = fetch_xml("/current")
            root_s = fetch_xml("/sample")

            if root_c and root_s:
                current_data          = parse_current(root_c)
                axes_data, rpm_sample = parse_samples(root_s)
                rpm = rpm_sample or current_data.get("spindle_rpm")
                save_sample(current_data, axes_data, spindle_rpm_override=rpm)
            else:
                log.warning("Skipped cycle — MTConnect endpoint not reachable")

        except Exception as e:
            log.error("Poller error: %s", e, exc_info=True)

        elapsed   = time.monotonic() - t0
        sleep_for = max(0.0, CFG["poll_interval"] - elapsed)
        time.sleep(sleep_for)

    log.info("Poller stopped.")


def start_poller():
    """Start (or restart) the background poll thread."""
    global _poller_thread, _poller_active

    # Stop existing thread if running
    if _poller_active:
        _poller_active = False
        if _poller_thread:
            _poller_thread.join(timeout=3)

    _poller_active = True
    _poller_thread = threading.Thread(
        target=poller_loop, daemon=True, name="mtc-poller"
    )
    _poller_thread.start()
    CFG["connected"] = True

# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

# ── Connection setup — called first by Streamlit frontend ────────────────────
@app.route("/api/connect", methods=["POST", "OPTIONS"])
def api_connect():
    """
    Receive connection parameters from the frontend and start the poller.

    Expected JSON body:
        {
            "mtc_host":  "192.168.1.100",
            "mtc_port":  5000,
            "interval":  1.0
        }

    Returns probe result (connected / error) plus device description.
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200

    body = request.get_json(silent=True) or {}
    host     = body.get("mtc_host", "").strip()
    port     = int(body.get("mtc_port", 5000))
    interval = float(body.get("interval", 1.0))

    if not host:
        return jsonify({"ok": False, "error": "mtc_host is required"}), 400

    # Test reachability by fetching /probe
    test_url = f"http://{host}:{port}/probe"
    try:
        r = requests.get(test_url, timeout=4)
        r.raise_for_status()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    # Update global config and (re)start poller
    CFG["mtc_host"]      = host
    CFG["mtc_port"]      = port
    CFG["poll_interval"] = interval

    init_db()        # Ensure DB tables exist
    start_poller()   # Begin collecting data

    log.info("Connected to %s:%s", host, port)
    return jsonify({
        "ok":       True,
        "endpoint": f"http://{host}:{port}",
        "db_path":  CFG["db_path"],
    })


# ── Latest snapshot — primary endpoint for the live dashboard ────────────────
@app.route("/api/latest")
def api_latest():
    """
    Returns the most recent machine state joined with axes, tool, and sensor.
    Called by Streamlit every second to refresh the live view.
    """
    if not CFG["connected"]:
        return jsonify({"error": "not connected"}), 503

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM machine_samples ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if not row:
            return jsonify({"error": "no data yet"}), 404

        sample = dict(row)

        # Axis positions and velocities for this sample
        axes = conn.execute("""
            SELECT axis, position_mm, velocity_mm_s, ts
            FROM axis_positions WHERE sample_id = ?
        """, (sample["id"],)).fetchall()
        sample["axes"] = [dict(a) for a in axes]

        # Tool state
        tool = conn.execute("""
            SELECT tool_number, station, spindle_num, max_power_kw
            FROM tool_state WHERE sample_id = ?
        """, (sample["id"],)).fetchone()
        sample["tool"] = dict(tool) if tool else {}

        # Latest current sensor reading (if any)
        sensor = conn.execute(
            "SELECT * FROM current_sensor ORDER BY id DESC LIMIT 1"
        ).fetchone()
        sample["current_sensor"] = dict(sensor) if sensor else None

    return jsonify(sample)


# ── Trajectory — XYZ position history for path plots ─────────────────────────
@app.route("/api/trajectory")
def api_trajectory():
    """
    Returns up to `limit` XYZ coordinate triples, pivoted from axis_positions.
    Used by Streamlit scatter/line plots for XY, XZ, YZ views.
    """
    limit = int(request.args.get("limit", 200))
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.id, s.ts,
                   MAX(CASE WHEN a.axis='X' THEN a.position_mm END) AS x,
                   MAX(CASE WHEN a.axis='Y' THEN a.position_mm END) AS y,
                   MAX(CASE WHEN a.axis='Z' THEN a.position_mm END) AS z
            FROM machine_samples s
            JOIN axis_positions a ON a.sample_id = s.id
            WHERE a.axis IN ('X','Y','Z')
            GROUP BY s.id
            ORDER BY s.id DESC LIMIT ?
        """, (limit,)).fetchall()
        rows = list(reversed(rows))
    return jsonify([dict(r) for r in rows])


# ── Spindle RPM history ───────────────────────────────────────────────────────
@app.route("/api/history/spindle")
def api_spindle_history():
    """Time series of spindle RPM. Query params: limit (int), since (ISO timestamp)."""
    limit = int(request.args.get("limit", 300))
    since = request.args.get("since")
    with get_db() as conn:
        if since:
            rows = conn.execute("""
                SELECT ts, spindle_rpm FROM machine_samples
                WHERE ts > ? ORDER BY id ASC LIMIT ?
            """, (since, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT ts, spindle_rpm FROM machine_samples
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            rows = list(reversed(rows))
    return jsonify([dict(r) for r in rows])


# ── Axis position + velocity history ─────────────────────────────────────────
@app.route("/api/history/axes")
def api_axes_history():
    """Time series for a single axis. Query params: axis (default X), limit, since."""
    axis  = request.args.get("axis", "X").upper()
    limit = int(request.args.get("limit", 300))
    since = request.args.get("since")
    with get_db() as conn:
        if since:
            rows = conn.execute("""
                SELECT ts, position_mm, velocity_mm_s FROM axis_positions
                WHERE axis=? AND ts>? ORDER BY id ASC LIMIT ?
            """, (axis, since, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT ts, position_mm, velocity_mm_s FROM axis_positions
                WHERE axis=? ORDER BY id DESC LIMIT ?
            """, (axis, limit)).fetchall()
            rows = list(reversed(rows))
    return jsonify([dict(r) for r in rows])


# ── Aggregate statistics ───────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    """Aggregate statistics over all recorded data."""
    with get_db() as conn:
        machine = conn.execute("""
            SELECT COUNT(*)                    AS total_samples,
                   MIN(ts)                     AS first_ts,
                   MAX(ts)                     AS last_ts,
                   ROUND(AVG(spindle_rpm), 1)  AS avg_spindle_rpm,
                   ROUND(MAX(spindle_rpm), 1)  AS max_spindle_rpm,
                   MAX(cycle_time_s)           AS max_cycle_time_s,
                   MAX(machine_runtime_s)      AS total_runtime_s,
                   MAX(m30_counter)            AS total_m30_count
            FROM machine_samples
        """).fetchone()
        axes = conn.execute("""
            SELECT axis,
                   ROUND(MIN(position_mm), 3)        AS pos_min,
                   ROUND(MAX(position_mm), 3)        AS pos_max,
                   ROUND(AVG(ABS(velocity_mm_s)), 3) AS avg_velocity
            FROM axis_positions GROUP BY axis
        """).fetchall()
    return jsonify({
        "machine": dict(machine) if machine else {},
        "axes":    [dict(r) for r in axes],
    })


# ── Current sensor history ─────────────────────────────────────────────────────
@app.route("/api/history/sensor")
def api_sensor_history():
    """Current sensor readings history."""
    limit = int(request.args.get("limit", 300))
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM current_sensor ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        rows = list(reversed(rows))
    return jsonify([dict(r) for r in rows])


# ── Current sensor ingest (POST) ───────────────────────────────────────────────
@app.route("/api/sensor", methods=["POST", "OPTIONS"])
def api_sensor_ingest():
    """
    Accept real-time current sensor data from external hardware.
    Expected JSON: { "spindle_A": 12.4, "x_axis_A": 3.1, "y_axis_A": 2.7 }
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400
    save_current_sensor(
        data.get("spindle_A"),
        data.get("x_axis_A"),
        data.get("y_axis_A"),
    )
    return jsonify({"status": "saved"}), 201


# ── MTConnect transparent proxy ───────────────────────────────────────────────
#
# Problem: Browsers block direct fetch() calls to the MTConnect agent because
# the Haas NGC controller does not send Access-Control-Allow-Origin headers.
# This is a CORS (Cross-Origin Resource Sharing) restriction enforced by
# every modern browser.
#
# Solution: The browser fetches /proxy/<path> on THIS backend instead.
# This backend (running server-side) forwards the request to the MTConnect
# agent, receives the XML, and re-serves it with the correct CORS headers.
# Server-to-server HTTP requests are never subject to browser CORS rules.
#
# Routes:
#   GET /proxy/current  →  http://<mtc_host>:<mtc_port>/current  (XML)
#   GET /proxy/sample   →  http://<mtc_host>:<mtc_port>/sample   (XML)
#   GET /proxy/probe    →  http://<mtc_host>:<mtc_port>/probe    (XML)
#   GET /proxy/assets   →  http://<mtc_host>:<mtc_port>/assets   (XML)
#
# The frontend (app.js) uses these proxy routes whenever BACKEND_URL is set,
# falling back to direct agent access only if the backend is unreachable.
# ---------------------------------------------------------------------------

@app.route("/proxy/<path:mtc_path>")
def mtc_proxy(mtc_path):
    """
    Transparent reverse proxy for all MTConnect XML endpoints.

    Forwards the request to the configured MTConnect agent, streams the raw
    XML response back to the browser, and injects CORS headers so the browser
    accepts it.

    Query parameters (e.g. ?from=1234&count=100) are forwarded unchanged.
    """
    # Guard: refuse proxy requests before /api/connect has been called
    if not CFG["mtc_host"]:
        return (
            "<error>Backend not connected to MTConnect agent. "
            "Call POST /api/connect first.</error>",
            503,
            {"Content-Type": "application/xml"},
        )

    # Build the target URL, forwarding any query string from the browser
    target = f"http://{CFG['mtc_host']}:{CFG['mtc_port']}/{mtc_path}"
    params = dict(request.args)   # preserve ?from=, ?count=, etc.

    try:
        # Forward the request server-side — no browser CORS restriction here
        resp = requests.get(target, params=params, timeout=5)
        resp.raise_for_status()

        # Return the raw XML with CORS header added
        return (
            resp.content,
            resp.status_code,
            {
                "Content-Type":                resp.headers.get("Content-Type", "application/xml"),
                "Access-Control-Allow-Origin": "*",    # Allows any browser origin
                "Cache-Control":               "no-store",
            },
        )

    except requests.Timeout:
        log.warning("Proxy timeout: %s", target)
        return "<error>MTConnect agent timed out</error>", 504, {"Content-Type": "application/xml"}

    except requests.ConnectionError as e:
        log.warning("Proxy connection error: %s — %s", target, e)
        return "<error>Cannot reach MTConnect agent</error>", 502, {"Content-Type": "application/xml"}

    except requests.HTTPError as e:
        log.warning("Proxy HTTP error: %s — %s", target, e)
        return f"<error>Agent returned {resp.status_code}</error>", resp.status_code, {"Content-Type": "application/xml"}


# ── Health check ───────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    """Health check: returns backend config and DB row counts."""
    counts = {}
    try:
        with get_db() as conn:
            counts = {
                "machine_samples": conn.execute("SELECT COUNT(*) FROM machine_samples").fetchone()[0],
                "axis_positions":  conn.execute("SELECT COUNT(*) FROM axis_positions").fetchone()[0],
                "current_sensor":  conn.execute("SELECT COUNT(*) FROM current_sensor").fetchone()[0],
            }
    except Exception:
        pass
    return jsonify({
        "status":      "running",
        "connected":   CFG["connected"],
        "mtc_host":    CFG["mtc_host"],
        "mtc_port":    CFG["mtc_port"],
        "poll_interval": CFG["poll_interval"],
        "db_path":     CFG["db_path"],
        "rows":        counts,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # In Docker the DB volume is mounted at /data/
    # Tables are created lazily on first /api/connect
    log.info("Backend starting on 0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

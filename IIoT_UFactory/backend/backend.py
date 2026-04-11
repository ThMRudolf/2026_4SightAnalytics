"""
backend.py — UFactory 850 Dashboard Backend
============================================
Verbindung: Private Modbus TCP, Port 18333
Protokoll:  UFactory Private Protocol (nicht Standard-Modbus)
Start:      python backend.py
API:        http://localhost:5000
"""

import struct
import socket
import threading
import time
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS

# ═══════════════════════════════════════════════════
#  KONFIGURATION
# ═══════════════════════════════════════════════════
ROBOT_IP   = "192.168.1.185"   # ← IP-Adresse des UFactory 850 anpassen
ROBOT_PORT = 18333             # Private Modbus TCP Port
POLL_HZ    = 10                # Polling-Frequenz in Hz
TIMEOUT_S  = 2.0               # Socket-Timeout in Sekunden

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Erlaubt Zugriff vom Browser (gleicher Host oder separater Dev-Server)

# ═══════════════════════════════════════════════════
#  PRIVATES MODBUS-TCP-PROTOKOLL — HILFFUNKTIONEN
#
#  Frame-Aufbau (laut UFactory Docs):
#   Byte 0-1: Transaction ID (U16, Big-Endian)
#   Byte 2-3: Protocol ID    (U16, immer 0x0002)
#   Byte 4-5: Length         (U16, Anzahl der folgenden Bytes)
#   Byte 6:   Register       (U8)
#   Byte 7-n: Payload        (optional)
# ═══════════════════════════════════════════════════

_tid = 0
_tid_lock = threading.Lock()

def _next_tid() -> int:
    """Eindeutige, aufsteigende Transaction-ID (0x0000–0xFFFF)."""
    global _tid
    with _tid_lock:
        _tid = (_tid + 1) & 0xFFFF
        return _tid

def build_request(register: int, payload: bytes = b"") -> bytes:
    """Baut einen UFactory Private Modbus TCP Request-Frame auf."""
    tid     = _next_tid()
    proto   = 0x0002
    length  = 1 + len(payload)   # Register-Byte + Payload
    header  = struct.pack(">HHH", tid, proto, length)
    return header + bytes([register]) + payload

def parse_fp32_list(data: bytes, offset: int, count: int) -> list[float]:
    """Liest <count> FP32-Werte (Big-Endian) ab <offset> aus <data>."""
    values = []
    for i in range(count):
        pos = offset + i * 4
        if pos + 4 <= len(data):
            (v,) = struct.unpack_from(">f", data, pos)
            values.append(round(float(v), 4))
        else:
            values.append(0.0)
    return values

# ═══════════════════════════════════════════════════
#  ROBOT CONNECTION — THREAD-SICHERER SOCKET-POOL
# ═══════════════════════════════════════════════════

class RobotConnection:
    """
    Verwaltet eine persistente TCP-Verbindung zum Roboter.
    Reconnect bei Verbindungsverlust. Thread-sicher über Lock.
    """

    def __init__(self, ip: str, port: int):
        self.ip      = ip
        self.port    = port
        self._sock   = None
        self._lock   = threading.Lock()
        self._connected = False

    def _connect(self):
        """Baut Verbindung auf. Gibt True bei Erfolg zurück."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(TIMEOUT_S)
            s.connect((self.ip, self.port))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock      = s
            self._connected = True
            log.info(f"Verbunden mit {self.ip}:{self.port}")
            return True
        except (socket.error, OSError) as e:
            log.warning(f"Verbindung fehlgeschlagen: {e}")
            self._sock      = None
            self._connected = False
            return False

    def _disconnect(self):
        """Schließt den Socket sauber."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock      = None
        self._connected = False

    def send_recv(self, frame: bytes, expected_min: int = 7) -> bytes | None:
        """
        Sendet einen Frame und empfängt die Antwort.
        Bei Fehler: Reconnect und erneuter Versuch (max. 1×).
        Gibt None zurück wenn beide Versuche scheitern.
        """
        with self._lock:
            for attempt in range(2):
                try:
                    if not self._connected:
                        if not self._connect():
                            time.sleep(0.5)
                            continue
                    self._sock.sendall(frame)
                    # Antwort-Header lesen (6 Byte)
                    header = self._recv_exact(6)
                    if header is None:
                        raise ConnectionError("Header empfang fehlgeschlagen")
                    _tid_r, _proto, length = struct.unpack(">HHH", header)
                    # Payload lesen
                    payload = self._recv_exact(length) if length > 0 else b""
                    if payload is None:
                        raise ConnectionError("Payload empfang fehlgeschlagen")
                    return header + payload
                except (socket.error, ConnectionError, OSError) as e:
                    log.warning(f"Kommunikationsfehler (Versuch {attempt+1}): {e}")
                    self._disconnect()
            return None

    def _recv_exact(self, n: int) -> bytes | None:
        """Liest exakt <n> Bytes vom Socket."""
        buf = b""
        deadline = time.time() + TIMEOUT_S
        while len(buf) < n:
            if time.time() > deadline:
                return None
            try:
                chunk = self._sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except socket.timeout:
                return None
        return buf

    @property
    def connected(self) -> bool:
        return self._connected


# Globale Verbindungsinstanz
robot = RobotConnection(ROBOT_IP, ROBOT_PORT)

# ═══════════════════════════════════════════════════
#  REGISTER-ABFRAGEN
#  Alle Funktionen folgen dem Muster:
#    1. Frame aufbauen (build_request)
#    2. Senden + Empfangen (robot.send_recv)
#    3. Antwort parsen
# ═══════════════════════════════════════════════════

def get_motion_state() -> dict:
    """
    Register 13 (0x0D) — Bewegungsstatus
    Response Byte 7: 1=in motion, 2=sleep, 3=suspend, 4=stop
    """
    resp = robot.send_recv(build_request(0x0D))
    if resp and len(resp) >= 8:
        return {"motionState": resp[7]}
    return {"motionState": 0}


def get_error_warn() -> dict:
    """
    Register 15 (0x0F) — Fehler- und Warncode
    Response Byte 8: Fehlercode, Byte 9: Warncode
    """
    resp = robot.send_recv(build_request(0x0F))
    if resp and len(resp) >= 10:
        return {"errorCode": resp[8], "warnCode": resp[9]}
    return {"errorCode": 0, "warnCode": 0}


def get_cmd_buffer() -> dict:
    """
    Register 14 (0x0E) — Anzahl Befehle im Puffer
    Response Byte 9-10: U16 Anzahl
    """
    resp = robot.send_recv(build_request(0x0E))
    if resp and len(resp) >= 11:
        (count,) = struct.unpack_from(">H", resp, 9)
        return {"cmdBuf": count}
    return {"cmdBuf": 0}


def get_tcp_position() -> dict:
    """
    Register 41 (0x29) — Kartesische TCP-Position
    Response: State(1B) + X,Y,Z,Roll,Pitch,Yaw (je FP32, 6×4=24 Byte)
    """
    resp = robot.send_recv(build_request(0x29))
    if resp and len(resp) >= 32:
        # Byte 7: State, Byte 8–31: 6× FP32
        vals = parse_fp32_list(resp, 8, 6)
        return {"tcpPos": vals}
    return {"tcpPos": [0.0]*6}


def get_joint_positions() -> dict:
    """
    Register 42 (0x2A) — Gelenkwinkel J1–J6 (rad)
    Response: State(1B) + J1..J6 (je FP32, 7×4=28 Byte, J7 ignoriert)
    """
    resp = robot.send_recv(build_request(0x2A))
    if resp and len(resp) >= 36:
        vals = parse_fp32_list(resp, 8, 6)  # J1–J6, J7 weggelassen
        return {"joints": vals}
    return {"joints": [0.0]*6}


def get_servo_temperatures() -> dict:
    """
    Register 104 (0x68) — Servo-Temperaturen J1–J6 (°C)
    Laut Servo Module (101–115): Temperatur-Register
    Response: State(1B) + T1..T6 (je FP32)
    """
    resp = robot.send_recv(build_request(0x68))
    if resp and len(resp) >= 32:
        vals = parse_fp32_list(resp, 8, 6)
        return {"temps": [round(v, 1) for v in vals]}
    return {"temps": [0.0]*6}


def get_servo_currents() -> dict:
    """
    Register 103 (0x67) — Servo-Ströme J1–J6 (A)
    """
    resp = robot.send_recv(build_request(0x67))
    if resp and len(resp) >= 32:
        vals = parse_fp32_list(resp, 8, 6)
        return {"currents": [round(abs(v), 3) for v in vals]}
    return {"currents": [0.0]*6}


def get_ft_sensor() -> dict:
    """
    Register 200 (0xC8) — 6-Achs-Kraft/Momentensor
    Response: State(1B) + Fx,Fy,Fz,Tx,Ty,Tz (je FP32)
    """
    resp = robot.send_recv(build_request(0xC8))
    if resp and len(resp) >= 32:
        vals = parse_fp32_list(resp, 8, 6)
        return {"ft": [round(v, 3) for v in vals]}
    return {"ft": [0.0]*6}


def get_digital_io() -> dict:
    """
    Register 131 (0x83) — Digitale Eingänge (DI0–DI3)
    Register 133 (0x85) — Digitale Ausgänge (DO0–DO3)
    """
    result = {"di": [0]*4, "do_": [0]*4}

    # DI lesen
    resp_di = robot.send_recv(build_request(0x83))
    if resp_di and len(resp_di) >= 9:
        # Byte 8: Bitmaske der digitalen Eingänge
        mask = resp_di[8]
        result["di"] = [(mask >> i) & 1 for i in range(4)]

    # DO lesen
    resp_do = robot.send_recv(build_request(0x85))
    if resp_do and len(resp_do) >= 9:
        mask = resp_do[8]
        result["do_"] = [(mask >> i) & 1 for i in range(4)]

    return result


def set_motion_state(new_state: int) -> bool:
    """
    Register 12 (0x0C) — Bewegungsstatus setzen
    0=run, 3=suspend, 4=stop
    """
    frame = build_request(0x0C, bytes([new_state]))
    resp  = robot.send_recv(frame)
    return resp is not None and len(resp) >= 8


def clear_error() -> bool:
    """Register 16 (0x10) — Fehler löschen."""
    resp = robot.send_recv(build_request(0x10))
    return resp is not None


def clear_warning() -> bool:
    """Register 17 (0x11) — Warnung löschen."""
    resp = robot.send_recv(build_request(0x11))
    return resp is not None


def set_digital_output(channel: int, value: int) -> bool:
    """
    Register 135 (0x87) — Digitalen Ausgang setzen
    Payload: Byte 0 = Kanal (0–3), Byte 1 = Wert (0/1)
    """
    frame = build_request(0x87, bytes([channel, value & 1]))
    resp  = robot.send_recv(frame)
    return resp is not None

# ═══════════════════════════════════════════════════
#  HINTERGRUND-POLLING (10 Hz)
#  Cached State — reduziert Latenz bei API-Aufrufen
# ═══════════════════════════════════════════════════

_cached_state: dict = {
    "connected":   False,
    "motionState": 0,
    "errorCode":   0, "warnCode": 0,
    "cmdBuf":      0,
    "tcpPos":      [0.0]*6,
    "joints":      [0.0]*6,
    "temps":       [0.0]*6,
    "currents":    [0.0]*6,
    "ft":          [0.0]*6,
    "di":          [0]*4,
    "do_":         [0]*4,
    "rtt":         0.0,
    "tid":         0,
    "tcpSpeed":    0.0,
}
_state_lock = threading.Lock()

def _poll_loop():
    """
    Liest zyklisch alle relevanten Register aus.
    Aktualisiert _cached_state thread-sicher.
    Läuft als Daemon-Thread im Hintergrund.
    """
    log.info(f"Polling-Thread gestartet ({POLL_HZ} Hz → {1/POLL_HZ*1000:.0f} ms)")
    _prev_pos = [0.0]*6
    _prev_t   = time.time()

    while True:
        loop_start = time.time()

        update = {}

        # Verbindungsversuch wenn getrennt
        if not robot.connected:
            robot._connect()

        update["connected"] = robot.connected

        if robot.connected:
            try:
                update.update(get_motion_state())
                update.update(get_error_warn())
                update.update(get_cmd_buffer())
                update.update(get_tcp_position())
                update.update(get_joint_positions())
                update.update(get_servo_temperatures())
                update.update(get_servo_currents())
                update.update(get_ft_sensor())
                update.update(get_digital_io())

                # TCP-Geschwindigkeit berechnen (numerische Ableitung)
                now = time.time()
                dt  = now - _prev_t
                if dt > 0 and "tcpPos" in update:
                    pos  = update["tcpPos"]
                    dx   = pos[0] - _prev_pos[0]
                    dy   = pos[1] - _prev_pos[1]
                    dz   = pos[2] - _prev_pos[2]
                    spd  = ((dx**2 + dy**2 + dz**2)**0.5) / dt
                    update["tcpSpeed"] = round(spd, 1)
                    _prev_pos = pos[:]
                    _prev_t   = now

            except Exception as e:
                log.error(f"Polling-Fehler: {e}")

        # RTT messen
        rtt_ms = (time.time() - loop_start) * 1000
        update["rtt"] = round(rtt_ms, 2)
        update["tid"] = _cached_state.get("tid", 0) + 1

        with _state_lock:
            _cached_state.update(update)

        # Rest der Periode schlafen
        elapsed = time.time() - loop_start
        sleep_s = max(0, (1.0 / POLL_HZ) - elapsed)
        time.sleep(sleep_s)


# ═══════════════════════════════════════════════════
#  FLASK REST-API
# ═══════════════════════════════════════════════════

@app.route("/api/poll", methods=["GET"])
def api_poll():
    """
    Gibt den aktuellen gecachten Roboter-Zustand zurück.
    Wird vom Frontend alle 100 ms abgerufen.
    """
    with _state_lock:
        return jsonify(dict(_cached_state))


@app.route("/api/motion", methods=["POST"])
def api_motion():
    """
    Setzt den Bewegungsstatus.
    Body: {"state": 0|3|4}   0=run, 3=suspend, 4=stop
    """
    data  = request.get_json(force=True)
    s     = int(data.get("state", 0))
    if s not in (0, 3, 4):
        return jsonify({"ok": False, "error": "Ungültiger State (0/3/4)"}), 400
    ok = set_motion_state(s)
    log.info(f"Motion State → {s} ({'ok' if ok else 'fehler'})")
    return jsonify({"ok": ok})


@app.route("/api/clear_error", methods=["POST"])
def api_clear_error():
    """Löscht den aktuellen Fehlercode (Reg 16)."""
    ok = clear_error()
    log.info(f"Fehler gelöscht ({'ok' if ok else 'fehler'})")
    return jsonify({"ok": ok})


@app.route("/api/clear_warning", methods=["POST"])
def api_clear_warning():
    """Löscht die aktuelle Warnung (Reg 17)."""
    ok = clear_warning()
    log.info(f"Warnung gelöscht ({'ok' if ok else 'fehler'})")
    return jsonify({"ok": ok})


@app.route("/api/digital_out", methods=["POST"])
def api_digital_out():
    """
    Setzt einen digitalen Ausgang.
    Body: {"channel": 0–3, "value": 0|1}
    """
    data    = request.get_json(force=True)
    channel = int(data.get("channel", 0))
    value   = int(data.get("value", 0))
    if channel not in range(4):
        return jsonify({"ok": False, "error": "Kanal 0–3"}), 400
    ok = set_digital_output(channel, value)
    log.info(f"DO{channel} → {value} ({'ok' if ok else 'fehler'})")
    return jsonify({"ok": ok})


@app.route("/api/config", methods=["GET"])
def api_config():
    """Gibt die aktuelle Verbindungskonfiguration zurück."""
    return jsonify({
        "ip":   ROBOT_IP,
        "port": ROBOT_PORT,
        "pollHz": POLL_HZ
    })


@app.route("/", methods=["GET"])
def index():
    """Serviert das Dashboard (index.html muss im selben Ordner liegen)."""
    from flask import send_from_directory
    import os
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


# ═══════════════════════════════════════════════════
#  EINSTIEGSPUNKT
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    # Polling-Thread starten (Daemon → endet mit Hauptprozess)
    poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="robot-poll")
    poll_thread.start()

    log.info("=" * 50)
    log.info(f"  UFactory 850 Dashboard Backend")
    log.info(f"  Roboter:  {ROBOT_IP}:{ROBOT_PORT}")
    log.info(f"  API:      http://0.0.0.0:5000")
    log.info(f"  Dashboard: http://localhost:5000")
    log.info("=" * 50)

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

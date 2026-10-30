"""Persistent SQLite outbox with a background delivery worker.

Every message is stored before it is sent, so nothing is lost if WhatsApp is
unreachable or the process restarts. The table doubles as an audit log.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Callable

from .channels.base import Channel
from .models import DeliveryResult, RenderedMessage

log = logging.getLogger("com2wa.outbox")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created       REAL    NOT NULL,
    to_phone      TEXT    NOT NULL,
    alert_key     TEXT,
    kind          TEXT,
    payload       TEXT    NOT NULL,          -- RenderedMessage as JSON
    status        TEXT    NOT NULL DEFAULT 'pending',   -- pending / sent / failed
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_attempt  REAL    NOT NULL,
    last_error    TEXT,
    provider_id   TEXT,
    sent_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, next_attempt);
"""


class Outbox:
    def __init__(self, path: str, channel: Channel, *, max_attempts: int = 8,
                 base_backoff_s: float = 5.0, max_backoff_s: float = 600.0,
                 poll_interval_s: float = 1.0, clock: Callable[[], float] = time.time) -> None:
        self.channel = channel
        self.max_attempts = max_attempts
        self.base_backoff_s = base_backoff_s
        self.max_backoff_s = max_backoff_s
        self.poll_interval_s = poll_interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- enqueue
    def enqueue(self, msg: RenderedMessage) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO outbox(created, to_phone, alert_key, kind, payload, next_attempt) "
                "VALUES (?,?,?,?,?,?)",
                (self._clock(), msg.to, msg.alert_key, msg.kind, json.dumps(msg.to_dict()), 0.0),
            )
        self._wake.set()
        return int(cur.lastrowid)

    # --------------------------------------------------------------- delivery
    def process_due(self, limit: int = 20) -> int:
        """Send all due pending messages once. Returns number processed. Safe to call directly."""
        now = self._clock()
        with self._lock:
            rows = self._db.execute(
                "SELECT id, payload, attempts FROM outbox WHERE status='pending' AND next_attempt<=? "
                "ORDER BY id LIMIT ?", (now, limit)).fetchall()
        for row_id, payload, attempts in rows:
            msg = RenderedMessage.from_dict(json.loads(payload))
            try:
                res = self.channel.send(msg)
            except Exception as e:   # channel contract says no raise, but be defensive
                log.exception("Channel %s raised", self.channel.name)
                res = DeliveryResult(ok=False, retryable=True, error=f"exception: {e}")
            attempts += 1
            with self._lock:
                if res.ok:
                    self._db.execute(
                        "UPDATE outbox SET status='sent', attempts=?, provider_id=?, sent_at=?, last_error=NULL "
                        "WHERE id=?", (attempts, res.provider_id, self._clock(), row_id))
                    log.info("Sent #%d %s to +%s (%s)", row_id, msg.alert_key, msg.to, res.provider_id)
                elif res.retryable and attempts < self.max_attempts:
                    delay = min(self.base_backoff_s * 2 ** (attempts - 1), self.max_backoff_s)
                    self._db.execute(
                        "UPDATE outbox SET attempts=?, next_attempt=?, last_error=? WHERE id=?",
                        (attempts, self._clock() + delay, res.error, row_id))
                    log.warning("Retry #%d in %.0fs (attempt %d): %s", row_id, delay, attempts, res.error)
                else:
                    self._db.execute(
                        "UPDATE outbox SET status='failed', attempts=?, last_error=? WHERE id=?",
                        (attempts, res.error, row_id))
                    log.error("Failed #%d to +%s: %s", row_id, msg.to, res.error)
        return len(rows)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                while self.process_due():
                    if self._stop.is_set():
                        return
            except Exception:
                log.exception("Outbox worker error")
            self._wake.wait(self.poll_interval_s)
            self._wake.clear()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="com2wa-outbox", daemon=True)
        self._thread.start()

    def close(self, flush_timeout_s: float = 5.0) -> None:
        """Try to deliver what is due, then stop the worker. Undelivered rows stay pending."""
        deadline = time.monotonic() + flush_timeout_s
        while time.monotonic() < deadline and self.pending_due_count():
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------- inspection
    def pending_due_count(self) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM outbox WHERE status='pending' AND next_attempt<=?",
                (self._clock(),)).fetchone()[0]

    def rows(self, status: str | None = None, limit: int | None = None, newest_first: bool = False) -> list[dict]:
        sql = ("SELECT id, created, to_phone, alert_key, kind, status, attempts, last_error, provider_id, sent_at "
               "FROM outbox")
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY id DESC" if newest_first else " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            args += (int(limit),)
        with self._lock:
            cur = self._db.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

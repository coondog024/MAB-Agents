"""SQLite store for repair orders.

Deliberately simple: one row per RO holding a JSON blob, plus a message log.
Swap this for your management system's API (CCC ONE, Mitchell, Rome) when you
connect it; every tool goes through this module, so nothing else changes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from . import config
from .models import STAGES, Note, RepairOrder

SCHEMA = """
CREATE TABLE IF NOT EXISTS repair_orders (
    ro_number TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ro_number TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
"""


class Store:
    """One Store can be shared across threads: each thread gets its own SQLite
    connection to the file. An in-memory store uses a single shared connection
    (fine for tests and single-threaded CLI use)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else config.db_path()
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None
        self._write_lock = threading.RLock()   # read-modify-write of an RO blob must not interleave
        if str(self.path) == ":memory:":
            self._shared = _connect(":memory:")
        self.conn.executescript(SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = _connect(self.path)
        return c

    # ---- repair orders ----------------------------------------------------
    def insert(self, ro: RepairOrder) -> None:
        """Create a new RO; refuses to overwrite an existing number."""
        with self._write_lock:
            if self.get(ro.ro_number) is not None:
                raise ValueError(f"RO {ro.ro_number} already exists")
            self.upsert(ro)

    def next_ro_number(self, prefix: str) -> str:
        """Next free number as <prefix>-<seq>, one above the highest existing suffix (never reused)."""
        with self._write_lock:
            used = {r.ro_number for r in self.list(include_delivered=True)}
            suffixes = [int(n.rsplit("-", 1)[1]) for n in used if n.rsplit("-", 1)[-1].isdigit()]
            seq = max(suffixes, default=1000) + 1
            while f"{prefix}-{seq}" in used:
                seq += 1
            return f"{prefix}-{seq}"

    def mutate(self, ro_number: str, fn: Callable[[RepairOrder], None]) -> RepairOrder:
        """Atomically load an RO, apply fn, save it."""
        with self._write_lock:
            ro = self.require(ro_number)
            fn(ro)
            self.upsert(ro)
            return ro

    def upsert(self, ro: RepairOrder) -> None:
        self.conn.execute(
            "INSERT INTO repair_orders (ro_number, stage, data, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ro_number) DO UPDATE SET stage=excluded.stage, data=excluded.data, updated_at=excluded.updated_at",
            (ro.ro_number, ro.stage, json.dumps(ro.to_dict()), datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def get(self, ro_number: str) -> RepairOrder | None:
        row = self.conn.execute("SELECT data FROM repair_orders WHERE ro_number = ?", (ro_number,)).fetchone()
        return RepairOrder.from_dict(json.loads(row["data"])) if row else None

    def require(self, ro_number: str) -> RepairOrder:
        ro = self.get(ro_number)
        if ro is None:
            raise KeyError(f"RO {ro_number} not found")
        return ro

    def list(self, include_delivered: bool = False) -> list[RepairOrder]:
        rows = self.conn.execute("SELECT data FROM repair_orders ORDER BY ro_number").fetchall()
        ros = [RepairOrder.from_dict(json.loads(r["data"])) for r in rows]
        if not include_delivered:
            ros = [r for r in ros if r.stage != "delivered"]
        return ros

    def set_stage(self, ro_number: str, stage: str, author: str = "system", note: str = "") -> RepairOrder:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage '{stage}'. Valid: {', '.join(STAGES)}")

        def apply(ro: RepairOrder) -> None:
            now = datetime.now().isoformat(timespec="seconds")
            previous = ro.stage
            ro.stage = stage
            ro.stage_changed = now
            if stage == "checked_in" and not ro.dropped_off:
                ro.dropped_off = now[:10]
            if stage == "delivered":
                ro.delivered = now[:10]
            ro.notes.append(Note(at=now, author=author, text=note or f"Stage {previous} -> {stage}"))

        return self.mutate(ro_number, apply)

    def add_note(self, ro_number: str, text: str, author: str = "agent") -> RepairOrder:
        return self.mutate(ro_number, lambda ro: ro.notes.append(Note(at=datetime.now().isoformat(timespec="seconds"), author=author, text=text)))

    def set_promised(self, ro_number: str, new_date: str, reason: str, author: str = "front-desk") -> RepairOrder:
        date.fromisoformat(new_date)  # validate

        def apply(ro: RepairOrder) -> None:
            old = ro.promised
            ro.promised = new_date
            ro.notes.append(Note(at=datetime.now().isoformat(timespec="seconds"), author=author, text=f"Promised {old or 'TBD'} -> {new_date}: {reason}"))

        return self.mutate(ro_number, apply)

    def find(self, query: str, include_delivered: bool = True) -> list[RepairOrder]:
        """Look up ROs by RO number, phone digits, customer name or plate-free vehicle text."""
        q = query.strip().lower()
        digits = "".join(ch for ch in q if ch.isdigit())
        out = []
        for ro in self.list(include_delivered=include_delivered):
            phone_digits = "".join(ch for ch in ro.customer.phone if ch.isdigit())
            if (
                q == ro.ro_number.lower()
                or (len(digits) >= 4 and digits in phone_digits)
                or (len(q) >= 3 and q in ro.customer.name.lower())
                or (len(q) >= 3 and q in ro.vehicle.label().lower())
                or (len(q) >= 6 and q in ro.vehicle.vin.lower())
            ):
                out.append(ro)
        return out

    # ---- message log ------------------------------------------------------
    def log_message(self, ro_number: str, channel: str, recipient: str, body: str, status: str = "draft") -> int:
        cur = self.conn.execute(
            "INSERT INTO messages (ro_number, channel, recipient, body, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ro_number, channel, recipient, body, status, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def mark_sent(self, message_id: int, ro_number: str) -> None:
        """A draft became a real message: flip the status and stamp the send time.
        The id must belong to the given RO."""
        cur = self.conn.execute(
            "UPDATE messages SET status = 'sent', created_at = ? WHERE id = ? AND ro_number = ?",
            (datetime.now().isoformat(timespec="seconds"), message_id, ro_number),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"No draft #{message_id} on RO {ro_number}")

    def last_messages(self, status: str | None = "sent") -> dict[str, dict]:
        """Most recent message per RO. Default counts only messages a human marked sent;
        drafts never count as contact."""
        where = "WHERE status = ?" if status else ""
        params = (status,) if status else ()
        rows = self.conn.execute(
            f"SELECT m.* FROM messages m JOIN (SELECT ro_number, MAX(id) AS id FROM messages {where} GROUP BY ro_number) x ON x.id = m.id",
            params,
        ).fetchall()
        return {r["ro_number"]: dict(r) for r in rows}

    def messages(self, ro_number: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM messages WHERE ro_number = ? ORDER BY id", (ro_number,)).fetchall()
        return [dict(r) for r in rows]

    # ---- bulk --------------------------------------------------------------
    def load_many(self, ros: Iterable[RepairOrder]) -> int:
        n = 0
        for ro in ros:
            self.upsert(ro)
            n += 1
        return n

    def load_sample(self, today: date | None = None) -> int:
        """Load the bundled demo ROs, shifting every date so the newest activity is 'today'."""
        sample = Path(__file__).with_name("data") / "sample_ros.json"
        data = json.loads(sample.read_text(encoding="utf-8"))
        base = date.fromisoformat(data["base_date"])
        delta = (today or date.today()) - base
        return self.load_many(RepairOrder.from_dict(_shift_dates(d, delta)) for d in data["ros"])

    def close(self) -> None:
        self.conn.close()
        if self._shared is None:
            self._local.conn = None


def _connect(path: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


DATE_FIELDS = {"opened", "dropped_off", "promised", "delivered", "stage_changed", "eta", "at"}


def _shift_dates(obj, delta: timedelta):
    """Recursively shift ISO dates/datetimes in the known date fields by delta."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DATE_FIELDS and isinstance(v, str) and v:
                if "T" in v:
                    out[k] = (datetime.fromisoformat(v) + delta).isoformat(timespec="seconds")
                else:
                    out[k] = (date.fromisoformat(v) + delta).isoformat()
            else:
                out[k] = _shift_dates(v, delta)
        return out
    if isinstance(obj, list):
        return [_shift_dates(x, delta) for x in obj]
    return obj

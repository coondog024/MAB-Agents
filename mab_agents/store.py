"""SQLite store for repair orders.

Deliberately simple: one row per RO holding a JSON blob, plus a message log.
Swap this for your management system's API (CCC ONE, Mitchell, Rome) when you
connect it; every tool goes through this module, so nothing else changes.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

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
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else config.db_path()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- repair orders ----------------------------------------------------
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
            raise KeyError(f"RO {ro_number} not found. Known ROs: {', '.join(r.ro_number for r in self.list())}")
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
        ro = self.require(ro_number)
        now = datetime.now().isoformat(timespec="seconds")
        previous = ro.stage
        ro.stage = stage
        ro.stage_changed = now
        if stage == "checked_in" and not ro.dropped_off:
            ro.dropped_off = now[:10]
        if stage == "delivered":
            ro.delivered = now[:10]
        ro.notes.append(Note(at=now, author=author, text=note or f"Stage {previous} -> {stage}"))
        self.upsert(ro)
        return ro

    def add_note(self, ro_number: str, text: str, author: str = "agent") -> RepairOrder:
        ro = self.require(ro_number)
        ro.notes.append(Note(at=datetime.now().isoformat(timespec="seconds"), author=author, text=text))
        self.upsert(ro)
        return ro

    # ---- message log ------------------------------------------------------
    def log_message(self, ro_number: str, channel: str, recipient: str, body: str, status: str = "draft") -> int:
        cur = self.conn.execute(
            "INSERT INTO messages (ro_number, channel, recipient, body, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ro_number, channel, recipient, body, status, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return int(cur.lastrowid)

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

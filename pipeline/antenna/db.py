"""SQLite storage. Raw signals land here first; everything else is derived."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import DB_PATH
from .models import Signal

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats       TEXT
);

-- One row per observed fact. `fingerprint` makes re-runs idempotent.
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL UNIQUE,
    run_id       INTEGER REFERENCES runs(id),
    source       TEXT NOT NULL,
    family       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    url          TEXT NOT NULL,
    value        REAL,
    unit         TEXT,
    strength     REAL NOT NULL,
    metrics      TEXT NOT NULL DEFAULT '{}',
    series       TEXT NOT NULL DEFAULT '[]',
    people       TEXT NOT NULL DEFAULT '[]',
    text         TEXT,
    hint         TEXT NOT NULL,          -- EntityHint as JSON
    entity_id    INTEGER REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS signals_entity ON signals(entity_id);
CREATE INDEX IF NOT EXISTS signals_family ON signals(family, occurred_at);

CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'company',
    domain      TEXT,
    github      TEXT,
    one_liner   TEXT,
    description TEXT,
    location    TEXT,
    founded     TEXT,
    links       TEXT NOT NULL DEFAULT '{}',
    aliases     TEXT NOT NULL DEFAULT '[]',
    first_seen  TEXT
);
CREATE INDEX IF NOT EXISTS entities_domain ON entities(domain);
CREATE INDEX IF NOT EXISTS entities_github ON entities(github);

CREATE TABLE IF NOT EXISTS scores (
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    edge        REAL NOT NULL,
    momentum    REAL NOT NULL,
    fit         REAL NOT NULL,
    earliness   REAL NOT NULL,
    team        REAL NOT NULL,
    sector      TEXT NOT NULL,
    breakdown   TEXT NOT NULL,
    PRIMARY KEY (entity_id, run_id)
);

CREATE TABLE IF NOT EXISTS collector_runs (
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    seconds     REAL,
    emitted     INTEGER,
    inserted    INTEGER,
    error       TEXT,
    PRIMARY KEY (run_id, source)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def fingerprint(sig: Signal) -> str:
    """Stable identity for a signal so the same fact is stored once.

    A point event is one row per (source, kind, entity, evidence, day). A
    continuous measurement (a series with per-point strength, a reading
    with no date of its own, or a rolling count flagged `rolling`) is one
    row per (source, kind, entity, subject) that each run refreshes, so tomorrow's reading replaces
    today's instead of stacking beside it.
    """
    ident = sig.entity.domain or sig.entity.github or sig.entity.name.lower()
    continuous = (bool(sig.metrics.get("observed_only")) or bool(sig.metrics.get("rolling"))
                  or any("s" in p for p in sig.series))
    if continuous:
        subject = str(sig.metrics.get("repo") or sig.metrics.get("subject") or "")
        raw = "|".join([sig.source, sig.kind, ident, subject])
    else:
        raw = "|".join([sig.source, sig.kind, ident, sig.url, sig.occurred_at[:10]])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def insert_signals(conn: sqlite3.Connection, run_id: int, signals: Iterable[Signal]) -> tuple[int, int]:
    """Store signals. Returns (emitted, newly_inserted).

    A signal seen again on a later run refreshes its numbers in place, so
    velocity metrics stay current without duplicating the row.
    """
    emitted = inserted = 0
    observed = now_iso()
    for sig in signals:
        sig.validate()
        emitted += 1
        row = sig.to_row()
        fp = fingerprint(sig)
        if not conn.execute("SELECT 1 FROM signals WHERE fingerprint=?", (fp,)).fetchone():
            inserted += 1
        conn.execute(
            """
            INSERT INTO signals (fingerprint, run_id, source, family, kind, title,
                occurred_at, observed_at, url, value, unit, strength, metrics,
                series, people, text, hint)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                run_id=excluded.run_id, title=excluded.title, value=excluded.value,
                occurred_at=excluded.occurred_at, url=excluded.url, unit=excluded.unit,
                strength=excluded.strength, metrics=excluded.metrics,
                series=excluded.series, people=excluded.people, text=excluded.text,
                hint=excluded.hint
            """,
            (
                fp,
                run_id,
                sig.source,
                sig.family,
                sig.kind,
                sig.title.strip(),
                sig.occurred_at,
                observed,
                sig.url,
                sig.value,
                sig.unit,
                float(sig.strength),
                json.dumps(row["metrics"], default=str),
                json.dumps(row["series"], default=str),
                json.dumps(row["people"], default=str),
                (sig.text or "")[:6000] or None,
                json.dumps(row["entity"], default=str),
            ),
        )
    conn.commit()
    return emitted, inserted

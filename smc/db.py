"""Historique des setups détectés en SQLite.

Chaque setup détecté est stocké, alerté ou non (colonne `alerted`), pour
pouvoir comparer plus tard « ce qui a été alerté » vs « ce qui a été
backtesté ».
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path

from smc.core import Setup

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,          -- date/heure de détection (ISO 8601)
    pair TEXT NOT NULL,
    direction TEXT NOT NULL,           -- long / short
    zone_kind TEXT NOT NULL,           -- FVG / OB
    zone_top REAL NOT NULL,
    zone_bottom REAL NOT NULL,
    swept INTEGER NOT NULL,            -- 1 si sweep détecté
    sweep_side TEXT,
    sweep_level REAL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    rr REAL NOT NULL,
    alerted INTEGER NOT NULL DEFAULT 0,   -- 1 si envoyé sur Telegram
    comment TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_pair ON alerts (pair);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # migrations légères pour les bases créées avant l'ajout de ces colonnes
    for col, ddl in (("score", "INTEGER NOT NULL DEFAULT 0"),
                     ("strategy", "TEXT NOT NULL DEFAULT 'amd_asian'")):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    return conn


def insert_setup(conn: sqlite3.Connection, setup: Setup, alerted: bool) -> int:
    cur = conn.execute(
        """INSERT INTO alerts (created_at, pair, direction, zone_kind, zone_top,
               zone_bottom, swept, sweep_side, sweep_level, entry, sl, tp, rr,
               alerted, comment, score, strategy)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            setup.time.isoformat(), setup.pair, setup.direction,
            setup.zone.kind, setup.zone.top, setup.zone.bottom,
            1 if setup.sweep else 0,
            setup.sweep.side if setup.sweep else None,
            setup.sweep.level if setup.sweep else None,
            setup.entry, setup.sl, setup.tp, setup.rr,
            1 if alerted else 0, " | ".join(setup.comments),
            setup.score, setup.strategy,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def alerts_sent_today(conn: sqlite3.Connection, day: date | None = None) -> int:
    day = day or datetime.now().date()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE alerted=1 AND created_at LIKE ?",
        (f"{day.isoformat()}%",),
    ).fetchone()
    return int(row["n"])


def setup_already_stored(conn: sqlite3.Connection, pair: str, day: date) -> bool:
    """Anti-doublon : un seul setup stocké par paire et par jour."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE pair=? AND created_at LIKE ?",
        (pair, f"{day.isoformat()}%",),
    ).fetchone()
    return int(row["n"]) > 0


def recent_alerts(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()

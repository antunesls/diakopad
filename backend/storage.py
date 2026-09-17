"""SQLite storage for pad assignments and the sample catalog."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "diakopad.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    uploaded_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pads (
    pad_number INTEGER PRIMARY KEY,
    midi_note INTEGER NOT NULL,
    sample_id INTEGER,
    volume_db REAL NOT NULL DEFAULT 6,
    pan REAL NOT NULL DEFAULT 0,
    cutoff_hz REAL,
    FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS knob_mappings (
    cc_number INTEGER PRIMARY KEY,
    pad_number INTEGER NOT NULL,
    param TEXT NOT NULL CHECK (param IN ('volume', 'pan', 'cutoff')),
    UNIQUE (pad_number, param)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# sustain_mode=1: samples always play to completion once triggered
# (loop_mode=one_shot), ignoring how long the pad is held - the usual
# behaviour for one-shot drum/sample pads. sustain_mode=0: releasing the pad
# cuts the sound promptly (short ampeg_release) instead.
# velocity_sensitive=0: every hit plays at the pad's set volume regardless of
# how hard it's struck (amp_veltrack=0) - this was the fix for hits sounding
# too quiet. velocity_sensitive=1: harder hits are louder (amp_veltrack=100).
DEFAULT_SETTINGS = {"sustain_mode": "1", "velocity_sensitive": "0"}

# Default note layout: sequential from 36 (C1), matches a typical MPC-style
# performance preset. Overridden per pad via the API once real notes are
# captured from the SMC-PAD (see README).
DEFAULT_BASE_NOTE = 36

# volume_db/pan/cutoff_hz were added after the first release; ALTER TABLE ADD
# COLUMN is a no-op-safe migration for the SQLite file that's already
# deployed and has live data (pad assignments) on it.
_MIGRATIONS = [
    "ALTER TABLE pads ADD COLUMN volume_db REAL NOT NULL DEFAULT 6",
    "ALTER TABLE pads ADD COLUMN pan REAL NOT NULL DEFAULT 0",
    "ALTER TABLE pads ADD COLUMN cutoff_hz REAL",
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(pads)")}
    for stmt in _MIGRATIONS:
        col = stmt.split("ADD COLUMN")[1].split()[0]
        if col not in existing_cols:
            conn.execute(stmt)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        existing = conn.execute("SELECT COUNT(*) AS c FROM pads").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO pads (pad_number, midi_note, sample_id) VALUES (?, ?, NULL)",
                [(n, DEFAULT_BASE_NOTE + (n - 1)) for n in range(1, 17)],
            )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def list_pads() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT p.pad_number, p.midi_note, p.sample_id, p.volume_db, p.pan, p.cutoff_hz,
                   s.display_name, s.filename
            FROM pads p
            LEFT JOIN samples s ON s.id = p.sample_id
            ORDER BY p.pad_number
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_pad_mix(
    pad_number: int,
    volume_db: Optional[float] = None,
    pan: Optional[float] = None,
    cutoff_hz: Optional[float] = "__unset__",  # type: ignore[assignment]
) -> None:
    """Partial update. volume_db/pan: None means "leave unchanged". cutoff_hz
    is tri-state (unset sentinel vs None vs a float), since None is itself a
    valid value here (no filter / bypass)."""
    fields, values = [], []
    if volume_db is not None:
        fields.append("volume_db = ?")
        values.append(volume_db)
    if pan is not None:
        fields.append("pan = ?")
        values.append(pan)
    if cutoff_hz != "__unset__":
        fields.append("cutoff_hz = ?")
        values.append(cutoff_hz)
    if not fields:
        return
    conn = get_connection()
    try:
        values.append(pad_number)
        conn.execute(f"UPDATE pads SET {', '.join(fields)} WHERE pad_number = ?", values)
        conn.commit()
    finally:
        conn.close()


def set_pad_note(pad_number: int, midi_note: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pads SET midi_note = ? WHERE pad_number = ?",
            (midi_note, pad_number),
        )
        conn.commit()
    finally:
        conn.close()


def assign_sample(pad_number: int, sample_id: Optional[int]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pads SET sample_id = ? WHERE pad_number = ?",
            (sample_id, pad_number),
        )
        conn.commit()
    finally:
        conn.close()


def list_samples() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filename, display_name, uploaded_at FROM samples ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_sample(filename: str, display_name: str) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO samples (filename, display_name, uploaded_at) VALUES (?, ?, ?)",
            (filename, display_name, time.time()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_sample(sample_id: int) -> Optional[str]:
    """Removes the sample row and returns its filename, or None if unused pads still reference it."""
    conn = get_connection()
    try:
        in_use = conn.execute(
            "SELECT COUNT(*) AS c FROM pads WHERE sample_id = ?", (sample_id,)
        ).fetchone()["c"]
        if in_use:
            return None
        row = conn.execute(
            "SELECT filename FROM samples WHERE id = ?", (sample_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
        conn.commit()
        return row["filename"]
    finally:
        conn.close()


def list_knob_mappings() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT cc_number, pad_number, param FROM knob_mappings ORDER BY cc_number"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_knob_mapping(cc_number: int, pad_number: int, param: str) -> None:
    """Binds a CC number to a (pad, param) target, replacing any previous
    mapping that used either the same CC or the same target."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM knob_mappings WHERE cc_number = ?", (cc_number,))
        conn.execute(
            "DELETE FROM knob_mappings WHERE pad_number = ? AND param = ?",
            (pad_number, param),
        )
        conn.execute(
            "INSERT INTO knob_mappings (cc_number, pad_number, param) VALUES (?, ?, ?)",
            (cc_number, pad_number, param),
        )
        conn.commit()
    finally:
        conn.close()


def delete_knob_mapping(cc_number: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM knob_mappings WHERE cc_number = ?", (cc_number,))
        conn.commit()
    finally:
        conn.close()


def clear_knob_mappings() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM knob_mappings")
        conn.commit()
    finally:
        conn.close()


def get_knob_target(cc_number: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT pad_number, param FROM knob_mappings WHERE cc_number = ?",
            (cc_number,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_settings() -> dict:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {**DEFAULT_SETTINGS, **{r["key"]: r["value"] for r in rows}}
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()

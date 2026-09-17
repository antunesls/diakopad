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
    FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE SET NULL
);
"""

# Default note layout: sequential from 36 (C1), matches a typical MPC-style
# performance preset. Overridden per pad via the API once real notes are
# captured from the SMC-PAD (see README).
DEFAULT_BASE_NOTE = 36


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        existing = conn.execute("SELECT COUNT(*) AS c FROM pads").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO pads (pad_number, midi_note, sample_id) VALUES (?, ?, NULL)",
                [(n, DEFAULT_BASE_NOTE + (n - 1)) for n in range(1, 17)],
            )
        conn.commit()
    finally:
        conn.close()


def list_pads() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT p.pad_number, p.midi_note, p.sample_id,
                   s.display_name, s.filename
            FROM pads p
            LEFT JOIN samples s ON s.id = p.sample_id
            ORDER BY p.pad_number
            """
        ).fetchall()
        return [dict(r) for r in rows]
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

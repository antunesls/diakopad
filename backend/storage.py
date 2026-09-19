"""SQLite storage for pad assignments and the sample catalog."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "diakopad.db"

# Shared between the upload endpoint (app.py) and the bulk folder importer
# (import_tool.py) so both accept/name files the same way.
ALLOWED_SAMPLE_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif"}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_folder(folder: str) -> str:
    """Returns a safe logical sample-library path, never a disk path."""
    if not isinstance(folder, str):
        raise ValueError("folder must be a string")
    if folder.startswith(("/", "\\")):
        raise ValueError("folder must be relative")
    parts = folder.replace("\\", "/").split("/") if folder else []
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("invalid folder path")
    return "/".join(parts)

SCHEMA = """
-- folder is a '/'-separated relative path ("" = root) purely for browsing/
-- organization in the Sons tab - the actual file always lives flat in
-- backend/samples/ under its own uuid-suffixed filename (see import_tool.py
-- and app.py's upload_sound), avoiding deep-path issues when mirroring a
-- large imported library.
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
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

-- scope='global' rows have pad_number=NULL (e.g. the shared tempo). `param`
-- has no CHECK constraint: valid values depend on which effect plugin (if
-- any) currently occupies a pad's slot, which SQLite can't express as a
-- static constraint - validated in Python instead (engine/knob_registry.py).
CREATE TABLE IF NOT EXISTS knob_mappings (
    cc_number INTEGER PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('pad', 'global')),
    pad_number INTEGER,
    param TEXT NOT NULL,
    UNIQUE (scope, pad_number, param)
);

-- slot_index is 1-based (1..EFFECT_SLOTS_PER_PAD). plugin_id NULL = empty
-- slot. params is a JSON object {symbol: value} for whichever plugin (if
-- any) is loaded, keyed by the LV2 param symbols from effects_catalog.py.
CREATE TABLE IF NOT EXISTS pad_effects (
    pad_number INTEGER NOT NULL,
    slot_index INTEGER NOT NULL,
    plugin_id TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (pad_number, slot_index)
);

-- One shared 16-step pattern. active=0 rows aren't bootstrapped as absent -
-- every (pad_number, step_index) pair gets a row so toggling is a plain
-- UPDATE, never an upsert.
CREATE TABLE IF NOT EXISTS sequencer_steps (
    pad_number INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pad_number, step_index)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Performance kits: a named snapshot of the full pad state (assignment +
-- volume/pan/tone) and the per-pad effect chains, for quick switching
-- during a show. midi_note is NOT captured - it belongs to the physical
-- controller mapping, not to the kit's sound.
CREATE TABLE IF NOT EXISTS kits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kit_pads (
    kit_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    sample_id INTEGER,
    volume_db REAL NOT NULL DEFAULT 6,
    pan REAL NOT NULL DEFAULT 0,
    cutoff_hz REAL,
    PRIMARY KEY (kit_id, pad_number),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kit_effects (
    kit_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    slot_index INTEGER NOT NULL,
    plugin_id TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (kit_id, pad_number, slot_index),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);

-- Knob (CC) mappings captured with the kit, so switching kits also flips
-- the physical-knob layout that was set up for it (same one-knob-per-target,
-- one-target-per-knob semantics as the live knob_mappings table).
CREATE TABLE IF NOT EXISTS kit_knobs (
    kit_id INTEGER NOT NULL,
    cc_number INTEGER NOT NULL,
    scope TEXT NOT NULL,
    pad_number INTEGER,
    param TEXT NOT NULL,
    PRIMARY KEY (kit_id, cc_number),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);

-- Binds a physical MIDI signal (note or CC) to a fixed logical action, for
-- SMC-PAD controls that have no on-screen equivalent (the "Gravar" button,
-- the side arrow, the "bak" pad). One action can have several bindings
-- (e.g. both the arrow and "bak" pointing at "kit_next"); one signal can
-- only point at one action - re-learning a signal replaces its old binding
-- instead of adding a second one.
CREATE TABLE IF NOT EXISTS controller_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    midi_type TEXT NOT NULL CHECK (midi_type IN ('note', 'cc')),
    number INTEGER NOT NULL,
    UNIQUE (midi_type, number)
);

-- Named snapshots of the sequencer_steps grid (same relationship as
-- kits/kit_pads have to pads), so a set can flip between programmed
-- patterns instead of only ever editing the one live grid.
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pattern_steps (
    pattern_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pattern_id, pad_number, step_index),
    FOREIGN KEY (pattern_id) REFERENCES patterns(id) ON DELETE CASCADE
);
"""

EFFECT_SLOTS_PER_PAD = 3
SEQUENCER_STEPS = 16

# sustain_mode=1: samples always play to completion once triggered
# (loop_mode=one_shot), ignoring how long the pad is held - the usual
# behaviour for one-shot drum/sample pads. sustain_mode=0: releasing the pad
# cuts the sound promptly (short ampeg_release) instead.
# velocity_sensitive=0: every hit plays at the pad's set volume regardless of
# how hard it's struck (amp_veltrack=0) - this was the fix for hits sounding
# too quiet. velocity_sensitive=1: harder hits are louder (amp_veltrack=100).
# sequencer_bpm: the app's single global tempo, shared by the sequencer and
# the metronome (see engine/tempo.py). metronome_style/_signature: the
# metronome's click sound (engine/metronome_sounds.py) and time signature
# (engine/time_signatures.py). metronome_beats_per_bar predates the
# time-signature catalog and is no longer read - left as a harmless orphan
# on any already-deployed DB, same as reverb_send/delay_send above.
DEFAULT_SETTINGS = {
    "sustain_mode": "1",
    "velocity_sensitive": "0",
    "sequencer_bpm": "100",
    "metronome_style": "digital",
    "metronome_signature": "4_4",
    "master_volume": "100",
    "master_muted": "0",
    "master_limiter_enabled": "1",
    "master_limiter_threshold_db": "-1",
    # Which kit is currently loaded in the engine. Empty string = none/
    # unknown (fresh install, or a kit deleted while it was current) - kept
    # server-side so a hardware-triggered "next kit" knows where to advance
    # from without depending on any one browser tab's local state.
    "current_kit_id": "",
}

# Default note layout: sequential from 36 (C1), matches a typical MPC-style
# performance preset. Overridden per pad via the API once real notes are
# captured from the SMC-PAD (see README).
DEFAULT_BASE_NOTE = 36

# volume_db/pan/cutoff_hz were added after the first release; ALTER TABLE ADD
# COLUMN is a no-op-safe migration for the SQLite file that's already
# deployed and has live data (pad assignments) on it. (reverb_send/delay_send
# were added and then removed again in the same pre-release dev cycle as the
# scope-based knob_mappings rework below - never shipped, no migration needed
# for them; a local backend/diakopad.db predating this just keeps those two
# unused columns around harmlessly.)
_MIGRATIONS = [
    "ALTER TABLE pads ADD COLUMN volume_db REAL NOT NULL DEFAULT 6",
    "ALTER TABLE pads ADD COLUMN pan REAL NOT NULL DEFAULT 0",
    "ALTER TABLE pads ADD COLUMN cutoff_hz REAL",
]

# samples.folder was added to support importing a whole sample library while
# keeping its directory tree (see import_tool.py) - existing rows default to
# '' (root), same as a manually-uploaded sound.
_SAMPLES_MIGRATIONS = [
    "ALTER TABLE samples ADD COLUMN folder TEXT NOT NULL DEFAULT ''",
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(pads)")}
    for stmt in _MIGRATIONS:
        col = stmt.split("ADD COLUMN")[1].split()[0]
        if col not in existing_cols:
            conn.execute(stmt)

    existing_sample_cols = {row["name"] for row in conn.execute("PRAGMA table_info(samples)")}
    for stmt in _SAMPLES_MIGRATIONS:
        col = stmt.split("ADD COLUMN")[1].split()[0]
        if col not in existing_sample_cols:
            conn.execute(stmt)

    # knob_mappings gained a "scope" column (pad vs. global targets like
    # tempo) and dropped its param CHECK constraint (now validated in Python,
    # since valid params depend on which effect plugin occupies a pad's
    # slot). SQLite can't ALTER either of those in place, so any
    # already-deployed DB whose knob_mappings table predates "scope" gets
    # rebuilt-and-copied, defaulting existing rows to scope='pad'.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'knob_mappings'"
    ).fetchone()
    if row is not None and "scope" not in row["sql"]:
        conn.executescript(
            """
            CREATE TABLE knob_mappings_new (
                cc_number INTEGER PRIMARY KEY,
                scope TEXT NOT NULL CHECK (scope IN ('pad', 'global')),
                pad_number INTEGER,
                param TEXT NOT NULL,
                UNIQUE (scope, pad_number, param)
            );
            INSERT INTO knob_mappings_new (cc_number, scope, pad_number, param)
                SELECT cc_number, 'pad', pad_number, param FROM knob_mappings;
            DROP TABLE knob_mappings;
            ALTER TABLE knob_mappings_new RENAME TO knob_mappings;
            """
        )


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
        conn.executemany(
            "INSERT OR IGNORE INTO pad_effects (pad_number, slot_index, plugin_id) VALUES (?, ?, NULL)",
            [(n, slot) for n in range(1, 17) for slot in range(1, EFFECT_SLOTS_PER_PAD + 1)],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO sequencer_steps (pad_number, step_index, active) VALUES (?, ?, 0)",
            [(n, step) for n in range(1, 17) for step in range(SEQUENCER_STEPS)],
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
            "SELECT id, filename, display_name, folder, uploaded_at FROM samples "
            "ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def browse_samples(folder: str = "") -> dict:
    """One level of the sample folder tree: immediate subfolders of `folder`
    (root = "") plus the samples stored directly in `folder` itself. Cheap
    to compute in Python even at a few thousand distinct folders - no
    dedicated folders table needed."""
    folder = normalize_folder(folder)
    conn = get_connection()
    try:
        rows = conn.execute("SELECT DISTINCT folder FROM samples WHERE folder != ''").fetchall()
        prefix = f"{folder}/" if folder else ""
        subfolders = set()
        for r in rows:
            f = r["folder"]
            if folder:
                if not f.startswith(prefix):
                    continue
                remainder = f[len(prefix):]
            else:
                remainder = f
            subfolders.add(remainder.split("/")[0])

        samples = conn.execute(
            "SELECT id, filename, display_name, folder, uploaded_at FROM samples WHERE folder = ? "
            "ORDER BY display_name COLLATE NOCASE",
            (folder,),
        ).fetchall()
        return {"folders": sorted(subfolders), "samples": [dict(r) for r in samples]}
    finally:
        conn.close()


def add_sample(filename: str, display_name: str, folder: str = "") -> int:
    folder = normalize_folder(folder)
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO samples (filename, display_name, folder, uploaded_at) VALUES (?, ?, ?, ?)",
            (filename, display_name, folder, time.time()),
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
            "SELECT cc_number, scope, pad_number, param FROM knob_mappings ORDER BY cc_number"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_knob_mapping(cc_number: int, scope: str, pad_number: Optional[int], param: str) -> None:
    """Binds a CC number to a (scope, pad_number, param) target, replacing
    any previous mapping that used either the same CC or the same target."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM knob_mappings WHERE cc_number = ?", (cc_number,))
        conn.execute(
            "DELETE FROM knob_mappings WHERE scope = ? AND pad_number IS ? AND param = ?",
            (scope, pad_number, param),
        )
        conn.execute(
            "INSERT INTO knob_mappings (cc_number, scope, pad_number, param) VALUES (?, ?, ?, ?)",
            (cc_number, scope, pad_number, param),
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


def delete_knob_mappings_for_pad_param_prefix(pad_number: int, prefix: str) -> None:
    """Cascade-deletes any knob mapping whose param starts with `prefix` for
    this pad - used when an effect slot's plugin changes/empties, so a CC
    doesn't stay bound to a parameter that no longer exists."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM knob_mappings WHERE scope = 'pad' AND pad_number = ? AND param LIKE ? ESCAPE '\\'",
            (pad_number, prefix.replace("_", r"\_").replace("%", r"\%") + "%"),
        )
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
            "SELECT scope, pad_number, param FROM knob_mappings WHERE cc_number = ?",
            (cc_number,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_controller_bindings() -> dict[str, list[dict]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, action, midi_type, number FROM controller_bindings ORDER BY action, midi_type, number"
        ).fetchall()
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            grouped.setdefault(r["action"], []).append(
                {"id": r["id"], "midi_type": r["midi_type"], "number": r["number"]}
            )
        return grouped
    finally:
        conn.close()


def add_controller_binding(action: str, midi_type: str, number: int) -> int:
    """Binds a physical signal to an action, replacing any existing binding
    for that same signal (a signal only ever means one action) - but unlike
    knob mappings, does NOT clear other bindings already pointing at this
    action, since one action can have several signals (e.g. arrow + bak)."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM controller_bindings WHERE midi_type = ? AND number = ?",
            (midi_type, number),
        )
        cursor = conn.execute(
            "INSERT INTO controller_bindings (action, midi_type, number) VALUES (?, ?, ?)",
            (action, midi_type, number),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def delete_controller_binding(binding_id: int) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM controller_bindings WHERE id = ?", (binding_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_action_for_signal(midi_type: str, number: int) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT action FROM controller_bindings WHERE midi_type = ? AND number = ?",
            (midi_type, number),
        ).fetchone()
        return row["action"] if row else None
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


def list_pad_effects() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT pad_number, slot_index, plugin_id, params FROM pad_effects "
            "ORDER BY pad_number, slot_index"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"])
            result.append(d)
        return result
    finally:
        conn.close()


def set_pad_effect_slot(pad_number: int, slot_index: int, plugin_id: Optional[str]) -> None:
    """Assigns (or clears, if plugin_id is None) the plugin for one slot,
    resetting its stored params to an empty dict - callers repopulate them
    with the new plugin's defaults from effects_catalog.py."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pad_effects SET plugin_id = ?, params = '{}' WHERE pad_number = ? AND slot_index = ?",
            (plugin_id, pad_number, slot_index),
        )
        conn.commit()
    finally:
        conn.close()


def set_pad_effect_param(pad_number: int, slot_index: int, symbol: str, value: float) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT params FROM pad_effects WHERE pad_number = ? AND slot_index = ?",
            (pad_number, slot_index),
        ).fetchone()
        params = json.loads(row["params"]) if row else {}
        params[symbol] = value
        conn.execute(
            "UPDATE pad_effects SET params = ? WHERE pad_number = ? AND slot_index = ?",
            (json.dumps(params), pad_number, slot_index),
        )
        conn.commit()
    finally:
        conn.close()


def list_sequencer_steps() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT pad_number, step_index, active FROM sequencer_steps "
            "ORDER BY pad_number, step_index"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_sequencer_step(pad_number: int, step_index: int, active: bool) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sequencer_steps SET active = ? WHERE pad_number = ? AND step_index = ?",
            (1 if active else 0, pad_number, step_index),
        )
        conn.commit()
    finally:
        conn.close()


def clear_sequencer_steps() -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE sequencer_steps SET active = 0")
        conn.commit()
    finally:
        conn.close()


# ── Sequencer patterns ───────────────────────────────────────────────────────


def list_patterns() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, name, created_at FROM patterns ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_pattern(name: str, steps: list[dict]) -> int:
    """Captures the current sequencer grid (see list_sequencer_steps) as a
    named pattern. Saving over an existing name replaces its contents."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM patterns WHERE name = ?", (name,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO patterns (name, created_at) VALUES (?, ?)", (name, time.time())
            )
            pattern_id = int(cur.lastrowid)
        else:
            pattern_id = row["id"]
            conn.execute("DELETE FROM pattern_steps WHERE pattern_id = ?", (pattern_id,))
        conn.executemany(
            "INSERT INTO pattern_steps (pattern_id, pad_number, step_index, active) VALUES (?, ?, ?, ?)",
            [
                (pattern_id, s["pad_number"], s["step_index"], 1 if s["active"] else 0)
                for s in steps
            ],
        )
        conn.commit()
        return pattern_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pattern(pattern_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, created_at FROM patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        if row is None:
            return None
        steps = conn.execute(
            "SELECT pad_number, step_index, active FROM pattern_steps WHERE pattern_id = ? "
            "ORDER BY pad_number, step_index",
            (pattern_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "steps": [dict(r) for r in steps],
        }
    finally:
        conn.close()


def delete_pattern(pattern_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM patterns WHERE id = ?", (pattern_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def apply_pattern(pattern_id: int) -> Optional[dict]:
    """Copies a saved pattern onto the live sequencer_steps grid (single
    transaction). Returns the pattern dict, or None if it doesn't exist.
    Callers must also refresh engine.sequencer's in-memory cache afterwards
    (see app.py) - this only touches the DB."""
    pattern = get_pattern(pattern_id)
    if pattern is None:
        return None
    conn = get_connection()
    try:
        conn.execute("UPDATE sequencer_steps SET active = 0")
        conn.executemany(
            "UPDATE sequencer_steps SET active = ? WHERE pad_number = ? AND step_index = ?",
            [(s["active"], s["pad_number"], s["step_index"]) for s in pattern["steps"]],
        )
        conn.commit()
        return pattern
    finally:
        conn.close()


# ── Performance kits ─────────────────────────────────────────────────────────


def list_kits() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, name, created_at FROM kits ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_kit(name: str, pads: list[dict], pad_effects: list[dict], knob_mappings: list[dict] | None = None) -> int:
    """Captures the current pad state (assignment + mix, the effect chains
    and the knob mappings) as a kit. Saving over an existing name replaces
    its contents."""
    conn = get_connection()
    try:
        kit_id = conn.execute("SELECT id FROM kits WHERE name = ?", (name,)).fetchone()
        if kit_id is None:
            cur = conn.execute(
                "INSERT INTO kits (name, created_at) VALUES (?, ?)", (name, time.time())
            )
            kit_id_val = int(cur.lastrowid)
        else:
            kit_id_val = kit_id["id"]
            conn.execute("DELETE FROM kit_pads WHERE kit_id = ?", (kit_id_val,))
            conn.execute("DELETE FROM kit_effects WHERE kit_id = ?", (kit_id_val,))
            conn.execute("DELETE FROM kit_knobs WHERE kit_id = ?", (kit_id_val,))
        conn.executemany(
            "INSERT INTO kit_pads (kit_id, pad_number, sample_id, volume_db, pan, cutoff_hz) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (kit_id_val, p["pad_number"], p.get("sample_id"), p.get("volume_db", 6),
                 p.get("pan", 0), p.get("cutoff_hz"))
                for p in pads
            ],
        )
        conn.executemany(
            "INSERT INTO kit_effects (kit_id, pad_number, slot_index, plugin_id, params) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    kit_id_val,
                    e["pad_number"],
                    e["slot_index"],
                    e.get("plugin_id"),
                    json.dumps(e.get("params") or {}),
                )
                for e in pad_effects
            ],
        )
        conn.executemany(
            "INSERT INTO kit_knobs (kit_id, cc_number, scope, pad_number, param) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    kit_id_val,
                    k["cc_number"],
                    k["scope"],
                    k["pad_number"],
                    k["param"],
                )
                for k in (knob_mappings or [])
            ],
        )
        conn.commit()
        return kit_id_val
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_kit(kit_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id, name, created_at FROM kits WHERE id = ?", (kit_id,)).fetchone()
        if row is None:
            return None
        pads = conn.execute(
            "SELECT pad_number, sample_id, volume_db, pan, cutoff_hz FROM kit_pads "
            "WHERE kit_id = ? ORDER BY pad_number",
            (kit_id,),
        ).fetchall()
        effects = conn.execute(
            "SELECT pad_number, slot_index, plugin_id, params FROM kit_effects "
            "WHERE kit_id = ? ORDER BY pad_number, slot_index",
            (kit_id,),
        ).fetchall()
        effects_parsed = []
        for r in effects:
            d = dict(r)
            d["params"] = json.loads(d["params"])
            effects_parsed.append(d)
        knobs = [
            dict(r)
            for r in conn.execute(
                "SELECT cc_number, scope, pad_number, param FROM kit_knobs "
                "WHERE kit_id = ? ORDER BY cc_number",
                (kit_id,),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "pads": [dict(r) for r in pads],
            "effects": effects_parsed,
            "knobs": knobs,
        }
    finally:
        conn.close()


def delete_kit(kit_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM kits WHERE id = ?", (kit_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def load_kit(kit_id: int) -> Optional[dict]:
    """Copies a kit onto the live pad state (single transaction). midi_note is
    preserved (physical controller mapping). The kit's knob mappings replace
    the live ones wholesale, so the physical knobs follow the kit - except a
    kit saved before knob mappings joined the snapshot (no kit_knobs rows),
    which leaves the current mappings untouched. Returns the kit dict, or
    None if the kit doesn't exist."""
    kit = get_kit(kit_id)
    if kit is None:
        return None
    conn = get_connection()
    try:
        for p in kit["pads"]:
            conn.execute(
                "UPDATE pads SET sample_id = ?, volume_db = ?, pan = ?, cutoff_hz = ? "
                "WHERE pad_number = ?",
                (p["sample_id"], p["volume_db"], p["pan"], p["cutoff_hz"], p["pad_number"]),
            )
        conn.execute("DELETE FROM pad_effects")
        conn.executemany(
            "INSERT INTO pad_effects (pad_number, slot_index, plugin_id, params) VALUES (?, ?, ?, ?)",
            [
                (
                    e["pad_number"],
                    e["slot_index"],
                    e["plugin_id"],
                    json.dumps(e.get("params") or {}),
                )
                for e in kit["effects"]
            ],
        )
        if kit["knobs"]:
            conn.execute("DELETE FROM knob_mappings")
            conn.executemany(
                "INSERT INTO knob_mappings (cc_number, scope, pad_number, param) VALUES (?, ?, ?, ?)",
                [
                    (k["cc_number"], k["scope"], k["pad_number"], k["param"])
                    for k in kit["knobs"]
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return kit

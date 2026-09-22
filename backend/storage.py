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

-- Performance scenes: a named snapshot of the full pad state (assignment +
-- volume/pan/tone) and the per-pad effect chains, for quick switching
-- during a show. midi_note is NOT captured - it belongs to the physical
-- controller mapping, not to the scene's sound.
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    -- 1 = scene available for live Performance navigation, 0 = saved but
    -- kept out of the live list (see list_active_scenes).
    active INTEGER NOT NULL DEFAULT 1
);

-- Global (non-pad) part of a scene snapshot: the shared tempo, the metronome
-- configuration and both transports' running state, so loading a scene
-- restores the whole set, not just the 16 pads.
CREATE TABLE IF NOT EXISTS scene_state (
    scene_id INTEGER PRIMARY KEY,
    sequencer_bpm TEXT NOT NULL DEFAULT '100',
    metronome_style TEXT NOT NULL DEFAULT 'digital',
    metronome_signature TEXT NOT NULL DEFAULT '4_4',
    metronome_running TEXT NOT NULL DEFAULT '0',
    sequencer_running TEXT NOT NULL DEFAULT '0',
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

-- Sequencer grid captured with the scene (16 x 16), same relationship
-- pattern_steps has to patterns.
CREATE TABLE IF NOT EXISTS scene_steps (
    scene_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scene_id, pad_number, step_index),
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_pads (
    scene_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    sample_id INTEGER,
    volume_db REAL NOT NULL DEFAULT 6,
    pan REAL NOT NULL DEFAULT 0,
    cutoff_hz REAL,
    PRIMARY KEY (scene_id, pad_number),
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_effects (
    scene_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    slot_index INTEGER NOT NULL,
    plugin_id TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (scene_id, pad_number, slot_index),
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

-- Knob (CC) mappings captured with the scene, so switching scenes also flips
-- the physical-knob layout that was set up for it (same one-knob-per-target,
-- one-target-per-knob semantics as the live knob_mappings table).
CREATE TABLE IF NOT EXISTS scene_knobs (
    scene_id INTEGER NOT NULL,
    cc_number INTEGER NOT NULL,
    scope TEXT NOT NULL,
    pad_number INTEGER,
    param TEXT NOT NULL,
    PRIMARY KEY (scene_id, cc_number),
    FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE
);

-- Kits: curated sound sets, distinct from scenes above - just a name plus
-- which sample goes on which pad (up to 16, fewer is fine - a kit that only
-- fills pads 1-8 has an "octapad feel"). No effects/knobs/volume/tempo, no
-- active flag (hardware kit-browse mode always walks the whole catalog by
-- category, there's no "live subset" concept the way scenes have one).
CREATE TABLE IF NOT EXISTS kits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT '',
    sort_index INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    source_path TEXT
);

CREATE TABLE IF NOT EXISTS kit_pads (
    kit_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    sample_id INTEGER,
    display_name TEXT,
    PRIMARY KEY (kit_id, pad_number),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE,
    FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE SET NULL
);

-- Binds a physical MIDI signal (note or CC) to a fixed logical action, for
-- SMC-PAD controls that have no on-screen equivalent (the "Gravar" button,
-- the side arrow, the "bak" pad). One action can have several bindings
-- (e.g. both the arrow and "bak" pointing at "scene_next"); one signal can
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
-- scenes/scene_pads have to pads), so a set can flip between programmed
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
    # Which scene is currently loaded in the engine. Empty string = none/
    # unknown (fresh install, or a scene deleted while it was current) - kept
    # server-side so a hardware-triggered "next scene" knows where to advance
    # from without depending on any one browser tab's local state.
    "current_scene_id": "",
    # Kit-browse mode (see app.py's _kit_browse_* state machine): whether
    # navigating/picking a candidate sound auto-plays it through the preview
    # engine. Off before a performance so browsing/confirming never makes
    # unwanted noise - a plain persistent toggle, not a MIDI-learned action.
    "kit_browse_preview_enabled": "1",
    # Which kit a per-pad kit-browse session last confirmed. Empty string =
    # none yet - a fresh session then starts at the first kit of the first
    # category. Kept server-side, same rationale as current_scene_id.
    "kit_browse_last_kit_id": "",
    # Whether stopping a Looper recording snaps the loop length to the
    # nearest whole bar (sequencer_bpm/metronome_signature), so repeats stay
    # in sync with the beat instead of drifting by however long the record
    # button happened to be held. See engine/looper.py's record_stop().
    "looper_quantize_enabled": "1",
    # Drops an immediate duplicate Note On from hardware while recording or
    # overdubbing. Value is milliseconds; zero disables the filter.
    "looper_duplicate_hit_window_ms": "30",
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

# scenes.active was added when scenes grew an active/inactive flag;
# already-deployed scenes default to active so the live list is unchanged.
_SCENES_MIGRATIONS = [
    "ALTER TABLE scenes ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
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

    existing_scene_cols = {row["name"] for row in conn.execute("PRAGMA table_info(scenes)")}
    for stmt in _SCENES_MIGRATIONS:
        col = stmt.split("ADD COLUMN")[1].split()[0]
        if col not in existing_scene_cols:
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


def _migrate_rename_old_kits_table(conn: sqlite3.Connection) -> None:
    """A DB from before the kit/scene split called the scene-snapshot table
    "kits" (id, name, created_at, active - no category/sort_index/
    source_path). Must run BEFORE executescript(SCHEMA), while that name is
    still free, so the new sound-set-shaped `kits` table can claim it -
    otherwise CREATE TABLE IF NOT EXISTS silently no-ops against the old
    shape. Idempotent: a DB that has already been migrated (or was never in
    the old shape) is a fast no-op.

    Also fixes controller_bindings rows learned under the old action names
    (kit_next/kit_prev) and the current_kit_id setting, so an
    already-configured physical scene-switch button doesn't silently stop
    working after the rename."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='kits'"
    ).fetchone()
    if row is None:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(kits)")}
    if "category" in cols or "active" not in cols:
        return  # already the new sound-set shape, or an unrecognized one

    scenes_exists = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenes'"
        ).fetchone()
        is not None
    )

    if not scenes_exists:
        # Common case: this DB predates the scene rename entirely - rename
        # the table and its children in place, preserving every row.
        conn.executescript(
            """
            ALTER TABLE kits RENAME TO scenes;
            ALTER TABLE kit_pads RENAME TO scene_pads;
            ALTER TABLE kit_effects RENAME TO scene_effects;
            ALTER TABLE kit_knobs RENAME TO scene_knobs;
            ALTER TABLE kit_state RENAME TO scene_state;
            ALTER TABLE kit_steps RENAME TO scene_steps;
            """
        )
        for table in ("scene_pads", "scene_effects", "scene_knobs", "scene_state", "scene_steps"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN kit_id TO scene_id")
    else:
        # A previous run of the new code already created an empty `scenes`
        # table before this migration existed. Recover: move any real rows
        # out of the old kits/kit_* tables if scenes is still empty, then
        # drop the old tables either way so the new sound-set-shaped `kits`
        # can be created.
        scenes_count = conn.execute("SELECT COUNT(*) AS c FROM scenes").fetchone()["c"]
        kits_count = conn.execute("SELECT COUNT(*) AS c FROM kits").fetchone()["c"]
        if kits_count > 0 and scenes_count == 0:
            conn.executescript(
                """
                INSERT INTO scenes (id, name, created_at, active)
                    SELECT id, name, created_at, active FROM kits;
                INSERT INTO scene_pads (scene_id, pad_number, sample_id, volume_db, pan, cutoff_hz)
                    SELECT kit_id, pad_number, sample_id, volume_db, pan, cutoff_hz FROM kit_pads;
                INSERT INTO scene_effects (scene_id, pad_number, slot_index, plugin_id, params)
                    SELECT kit_id, pad_number, slot_index, plugin_id, params FROM kit_effects;
                INSERT INTO scene_knobs (scene_id, cc_number, scope, pad_number, param)
                    SELECT kit_id, cc_number, scope, pad_number, param FROM kit_knobs;
                INSERT INTO scene_state (scene_id, sequencer_bpm, metronome_style, metronome_signature, metronome_running, sequencer_running)
                    SELECT kit_id, sequencer_bpm, metronome_style, metronome_signature, metronome_running, sequencer_running FROM kit_state;
                INSERT INTO scene_steps (scene_id, pad_number, step_index, active)
                    SELECT kit_id, pad_number, step_index, active FROM kit_steps;
                """
            )
        elif kits_count > 0 and scenes_count > 0:
            # Both hold real data - don't guess. Extremely unlikely (would
            # need this migration skipped across more than one upgrade of an
            # already-deployed instance); leave both tables in place for a
            # human to reconcile instead of risking data loss.
            import logging

            logging.getLogger("diakopad.storage").error(
                "storage: both 'kits' (old scene shape, %d rows) and "
                "'scenes' (%d rows) hold data - skipping the automatic "
                "rename migration, reconcile manually.",
                kits_count,
                scenes_count,
            )
            return
        conn.executescript(
            """
            DROP TABLE IF EXISTS kit_steps;
            DROP TABLE IF EXISTS kit_state;
            DROP TABLE IF EXISTS kit_knobs;
            DROP TABLE IF EXISTS kit_effects;
            DROP TABLE IF EXISTS kit_pads;
            DROP TABLE IF EXISTS kits;
            """
        )

    conn.execute("UPDATE controller_bindings SET action='scene_next' WHERE action='kit_next'")
    conn.execute("UPDATE controller_bindings SET action='scene_prev' WHERE action='kit_prev'")

    old_setting = conn.execute(
        "SELECT value FROM settings WHERE key='current_kit_id'"
    ).fetchone()
    if old_setting is not None:
        new_setting = conn.execute(
            "SELECT value FROM settings WHERE key='current_scene_id'"
        ).fetchone()
        if new_setting is None or not new_setting["value"]:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('current_scene_id', ?)",
                (old_setting["value"],),
            )
        conn.execute("DELETE FROM settings WHERE key='current_kit_id'")

    conn.commit()


def init_db() -> None:
    conn = get_connection()
    try:
        _migrate_rename_old_kits_table(conn)
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


def clear_all_pads() -> None:
    """Removes the sample assignment from every pad (mix/effects/knobs are
    left untouched - this is the bulk "clear the pads" action)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE pads SET sample_id = NULL")
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


# ── Performance scenes ───────────────────────────────────────────────────────


def list_scenes() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, created_at, active FROM scenes ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_active_scenes() -> list[dict]:
    """Scenes available for live Performance navigation (active = 1)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, created_at, active FROM scenes WHERE active = 1 ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_scene_active(scene_id: int, active: bool) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE scenes SET active = ? WHERE id = ?", (1 if active else 0, scene_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def save_scene(
    name: str,
    pads: list[dict],
    pad_effects: list[dict],
    knob_mappings: list[dict] | None = None,
    scene_state: dict | None = None,
    sequencer_steps: list[dict] | None = None,
) -> int:
    """Captures the current set state as a scene: pad assignments + mix, the
    effect chains, the knob mappings, the global tempo/metronome state and the
    sequencer grid. Saving over an existing name replaces its contents (the
    active flag is preserved)."""
    conn = get_connection()
    try:
        scene_id = conn.execute("SELECT id FROM scenes WHERE name = ?", (name,)).fetchone()
        if scene_id is None:
            cur = conn.execute(
                "INSERT INTO scenes (name, created_at) VALUES (?, ?)", (name, time.time())
            )
            scene_id_val = int(cur.lastrowid)
        else:
            scene_id_val = scene_id["id"]
            conn.execute("DELETE FROM scene_pads WHERE scene_id = ?", (scene_id_val,))
            conn.execute("DELETE FROM scene_effects WHERE scene_id = ?", (scene_id_val,))
            conn.execute("DELETE FROM scene_knobs WHERE scene_id = ?", (scene_id_val,))
            conn.execute("DELETE FROM scene_state WHERE scene_id = ?", (scene_id_val,))
            conn.execute("DELETE FROM scene_steps WHERE scene_id = ?", (scene_id_val,))
        conn.executemany(
            "INSERT INTO scene_pads (scene_id, pad_number, sample_id, volume_db, pan, cutoff_hz) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (scene_id_val, p["pad_number"], p.get("sample_id"), p.get("volume_db", 6),
                 p.get("pan", 0), p.get("cutoff_hz"))
                for p in pads
            ],
        )
        conn.executemany(
            "INSERT INTO scene_effects (scene_id, pad_number, slot_index, plugin_id, params) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    scene_id_val,
                    e["pad_number"],
                    e["slot_index"],
                    e.get("plugin_id"),
                    json.dumps(e.get("params") or {}),
                )
                for e in pad_effects
            ],
        )
        conn.executemany(
            "INSERT INTO scene_knobs (scene_id, cc_number, scope, pad_number, param) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    scene_id_val,
                    k["cc_number"],
                    k["scope"],
                    k["pad_number"],
                    k["param"],
                )
                for k in (knob_mappings or [])
            ],
        )
        if scene_state is not None:
            conn.execute(
                "INSERT INTO scene_state "
                "(scene_id, sequencer_bpm, metronome_style, metronome_signature, "
                "metronome_running, sequencer_running) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scene_id_val,
                    str(scene_state.get("sequencer_bpm", "100")),
                    str(scene_state.get("metronome_style", "digital")),
                    str(scene_state.get("metronome_signature", "4_4")),
                    "1" if scene_state.get("metronome_running") in (True, "1", 1) else "0",
                    "1" if scene_state.get("sequencer_running") in (True, "1", 1) else "0",
                ),
            )
        if sequencer_steps is not None:
            conn.executemany(
                "INSERT INTO scene_steps (scene_id, pad_number, step_index, active) "
                "VALUES (?, ?, ?, ?)",
                [
                    (scene_id_val, s["pad_number"], s["step_index"], 1 if s.get("active") else 0)
                    for s in sequencer_steps
                ],
            )
        conn.commit()
        return scene_id_val
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_scene(scene_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, created_at, active FROM scenes WHERE id = ?", (scene_id,)
        ).fetchone()
        if row is None:
            return None
        pads = conn.execute(
            "SELECT pad_number, sample_id, volume_db, pan, cutoff_hz FROM scene_pads "
            "WHERE scene_id = ? ORDER BY pad_number",
            (scene_id,),
        ).fetchall()
        effects = conn.execute(
            "SELECT pad_number, slot_index, plugin_id, params FROM scene_effects "
            "WHERE scene_id = ? ORDER BY pad_number, slot_index",
            (scene_id,),
        ).fetchall()
        effects_parsed = []
        for r in effects:
            d = dict(r)
            d["params"] = json.loads(d["params"])
            effects_parsed.append(d)
        knobs = [
            dict(r)
            for r in conn.execute(
                "SELECT cc_number, scope, pad_number, param FROM scene_knobs "
                "WHERE scene_id = ? ORDER BY cc_number",
                (scene_id,),
            ).fetchall()
        ]
        state_row = conn.execute(
            "SELECT sequencer_bpm, metronome_style, metronome_signature, "
            "metronome_running, sequencer_running FROM scene_state WHERE scene_id = ?",
            (scene_id,),
        ).fetchone()
        state = dict(state_row) if state_row is not None else None
        steps = [
            dict(r)
            for r in conn.execute(
                "SELECT pad_number, step_index, active FROM scene_steps "
                "WHERE scene_id = ? ORDER BY pad_number, step_index",
                (scene_id,),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "active": bool(row["active"]),
            "pads": [dict(r) for r in pads],
            "effects": effects_parsed,
            "knobs": knobs,
            "state": state,
            "steps": steps,
        }
    finally:
        conn.close()


def delete_scene(scene_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Kits (sound sets) ────────────────────────────────────────────────────────


def list_kit_categories() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM kits ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]
    finally:
        conn.close()


def _kit_pads(conn: sqlite3.Connection, kit_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT kp.pad_number, kp.sample_id, kp.display_name, s.filename "
        "FROM kit_pads kp LEFT JOIN samples s ON s.id = kp.sample_id "
        "WHERE kp.kit_id = ? ORDER BY kp.pad_number",
        (kit_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _kit_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "sort_index": row["sort_index"],
        "created_at": row["created_at"],
        "source_path": row["source_path"],
        "pads": _kit_pads(conn, row["id"]),
    }


def list_kits_in_category(category: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, category, sort_index, created_at, source_path FROM kits "
            "WHERE category = ? ORDER BY sort_index, name",
            (category,),
        ).fetchall()
        return [_kit_row_to_dict(conn, r) for r in rows]
    finally:
        conn.close()


def list_kits() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, category, sort_index, created_at, source_path FROM kits "
            "ORDER BY category, sort_index, name"
        ).fetchall()
        return [_kit_row_to_dict(conn, r) for r in rows]
    finally:
        conn.close()


def get_kit(kit_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, category, sort_index, created_at, source_path FROM kits WHERE id = ?",
            (kit_id,),
        ).fetchone()
        if row is None:
            return None
        return _kit_row_to_dict(conn, row)
    finally:
        conn.close()


def create_kit(
    name: str,
    category: str,
    sort_index: int,
    pads: list[dict],
    source_path: str | None = None,
) -> int:
    """Creates (or, if `name` already exists, replaces the contents of) a
    kit. Upsert-by-name, same convention as save_scene, so re-importing a
    pack after editing its manifest is safe to run again."""
    conn = get_connection()
    try:
        kit_row = conn.execute("SELECT id FROM kits WHERE name = ?", (name,)).fetchone()
        if kit_row is None:
            cur = conn.execute(
                "INSERT INTO kits (name, category, sort_index, created_at, source_path) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, category, sort_index, time.time(), source_path),
            )
            kit_id = int(cur.lastrowid)
        else:
            kit_id = kit_row["id"]
            conn.execute(
                "UPDATE kits SET category = ?, sort_index = ?, source_path = ? WHERE id = ?",
                (category, sort_index, source_path, kit_id),
            )
            conn.execute("DELETE FROM kit_pads WHERE kit_id = ?", (kit_id,))
        conn.executemany(
            "INSERT INTO kit_pads (kit_id, pad_number, sample_id, display_name) VALUES (?, ?, ?, ?)",
            [
                (kit_id, p["pad_number"], p.get("sample_id"), p.get("display_name"))
                for p in pads
            ],
        )
        conn.commit()
        return kit_id
    except Exception:
        conn.rollback()
        raise
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


def load_scene(scene_id: int) -> Optional[dict]:
    """Copies a scene onto the live state (single transaction). midi_note is
    preserved (physical controller mapping). The scene's knob mappings replace
    the live ones wholesale, so the physical knobs follow the scene - except a
    scene saved before knob mappings joined the snapshot (no scene_knobs rows),
    which leaves the current mappings untouched. The global tempo/metronome
    settings and the sequencer grid are restored too when the scene carries
    them (scenes saved before scenes grew a global state leave them as-is).
    Returns the scene dict, or None if it doesn't exist."""
    scene = get_scene(scene_id)
    if scene is None:
        return None
    conn = get_connection()
    try:
        for p in scene["pads"]:
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
                for e in scene["effects"]
            ],
        )
        if scene["knobs"]:
            conn.execute("DELETE FROM knob_mappings")
            conn.executemany(
                "INSERT INTO knob_mappings (cc_number, scope, pad_number, param) VALUES (?, ?, ?, ?)",
                [
                    (k["cc_number"], k["scope"], k["pad_number"], k["param"])
                    for k in scene["knobs"]
                ],
            )
        if scene["state"] is not None:
            for key in ("sequencer_bpm", "metronome_style", "metronome_signature"):
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, scene["state"][key]),
                )
        if scene["steps"]:
            active = {
                (s["pad_number"], s["step_index"]): 1 if s["active"] else 0
                for s in scene["steps"]
            }
            conn.execute("DELETE FROM sequencer_steps")
            conn.executemany(
                "INSERT INTO sequencer_steps (pad_number, step_index, active) VALUES (?, ?, ?)",
                [
                    (pad, step, active.get((pad, step), 0))
                    for pad in range(1, 17)
                    for step in range(SEQUENCER_STEPS)
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return scene

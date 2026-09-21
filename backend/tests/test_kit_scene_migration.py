import sqlite3
import tempfile
import unittest
from pathlib import Path

import storage

# Mirrors the pre-rename schema exactly (the "kits" table used to be what
# is now "scenes") - built by hand with raw SQL rather than via storage.SCHEMA,
# since that string only ever describes the CURRENT (post-rename) shape.
_OLD_SCHEMA = """
CREATE TABLE controller_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    midi_type TEXT NOT NULL CHECK (midi_type IN ('note', 'cc')),
    number INTEGER NOT NULL,
    UNIQUE (midi_type, number)
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE kits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE kit_pads (
    kit_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    sample_id INTEGER,
    volume_db REAL NOT NULL DEFAULT 6,
    pan REAL NOT NULL DEFAULT 0,
    cutoff_hz REAL,
    PRIMARY KEY (kit_id, pad_number),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);
CREATE TABLE kit_effects (
    kit_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    slot_index INTEGER NOT NULL,
    plugin_id TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (kit_id, pad_number, slot_index),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);
CREATE TABLE kit_knobs (
    kit_id INTEGER NOT NULL,
    cc_number INTEGER NOT NULL,
    scope TEXT NOT NULL,
    pad_number INTEGER,
    param TEXT NOT NULL,
    PRIMARY KEY (kit_id, cc_number),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);
CREATE TABLE kit_state (
    kit_id INTEGER PRIMARY KEY,
    sequencer_bpm TEXT NOT NULL DEFAULT '100',
    metronome_style TEXT NOT NULL DEFAULT 'digital',
    metronome_signature TEXT NOT NULL DEFAULT '4_4',
    metronome_running TEXT NOT NULL DEFAULT '0',
    sequencer_running TEXT NOT NULL DEFAULT '0',
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);
CREATE TABLE kit_steps (
    kit_id INTEGER NOT NULL,
    pad_number INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kit_id, pad_number, step_index),
    FOREIGN KEY (kit_id) REFERENCES kits(id) ON DELETE CASCADE
);
"""


class MigrationTestBase(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def _raw_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(storage.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


class PreRenameDatabaseMigrationTests(MigrationTestBase):
    """A DB that never saw the post-rename code at all (e.g. the Pi, still
    pending its own deploy) - "kits" is the only scene-shaped table, no
    "scenes" table exists yet."""

    def setUp(self):
        super().setUp()
        conn = self._raw_connection()
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO kits (id, name, created_at, active) VALUES (7, 'Show de Sabado', 111.0, 1)"
        )
        conn.execute(
            "INSERT INTO kit_pads (kit_id, pad_number, sample_id, volume_db, pan, cutoff_hz) "
            "VALUES (7, 1, NULL, 3.0, -0.2, NULL)"
        )
        conn.execute(
            "INSERT INTO controller_bindings (action, midi_type, number) VALUES ('kit_next', 'cc', 26)"
        )
        conn.execute(
            "INSERT INTO controller_bindings (action, midi_type, number) VALUES ('kit_prev', 'cc', 25)"
        )
        conn.execute("INSERT INTO settings (key, value) VALUES ('current_kit_id', '7')")
        conn.commit()
        conn.close()

    def test_real_scene_data_survives_the_rename(self):
        storage.init_db()
        scenes = storage.list_scenes()
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0]["name"], "Show de Sabado")
        scene = storage.get_scene(scenes[0]["id"])
        self.assertEqual(len(scene["pads"]), 1)
        self.assertEqual(scene["pads"][0]["pad_number"], 1)

    def test_controller_bindings_and_current_scene_setting_follow_the_rename(self):
        storage.init_db()
        bindings = storage.list_controller_bindings()
        self.assertIn("scene_next", bindings)
        self.assertIn("scene_prev", bindings)
        self.assertNotIn("kit_next", bindings)
        self.assertNotIn("kit_prev", bindings)
        settings = storage.get_settings()
        self.assertEqual(settings["current_scene_id"], "7")
        self.assertNotIn("current_kit_id", settings)

    def test_new_sound_set_kits_table_is_created_after_migration(self):
        storage.init_db()
        self.assertEqual(storage.list_kits(), [])
        kit_id = storage.create_kit("Teste", "01_electronic", 0, [])
        self.assertIsInstance(kit_id, int)


class AlreadyPartiallyUpgradedDatabaseMigrationTests(MigrationTestBase):
    """Recovery scenario: a previous run of the post-rename code already
    executed CREATE TABLE IF NOT EXISTS scenes (empty, since the old "kits"
    name was still taken) before this migration existed. Both tables exist;
    the old one has no real data to lose."""

    def setUp(self):
        super().setUp()
        conn = self._raw_connection()
        conn.executescript(_OLD_SCHEMA)
        conn.executescript(
            """
            CREATE TABLE scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE scene_pads (
                scene_id INTEGER NOT NULL,
                pad_number INTEGER NOT NULL,
                sample_id INTEGER,
                volume_db REAL NOT NULL DEFAULT 6,
                pan REAL NOT NULL DEFAULT 0,
                cutoff_hz REAL,
                PRIMARY KEY (scene_id, pad_number)
            );
            CREATE TABLE scene_effects (
                scene_id INTEGER NOT NULL, pad_number INTEGER NOT NULL,
                slot_index INTEGER NOT NULL, plugin_id TEXT,
                params TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (scene_id, pad_number, slot_index)
            );
            CREATE TABLE scene_knobs (
                scene_id INTEGER NOT NULL, cc_number INTEGER NOT NULL,
                scope TEXT NOT NULL, pad_number INTEGER, param TEXT NOT NULL,
                PRIMARY KEY (scene_id, cc_number)
            );
            CREATE TABLE scene_state (
                scene_id INTEGER PRIMARY KEY,
                sequencer_bpm TEXT NOT NULL DEFAULT '100',
                metronome_style TEXT NOT NULL DEFAULT 'digital',
                metronome_signature TEXT NOT NULL DEFAULT '4_4',
                metronome_running TEXT NOT NULL DEFAULT '0',
                sequencer_running TEXT NOT NULL DEFAULT '0'
            );
            CREATE TABLE scene_steps (
                scene_id INTEGER NOT NULL, pad_number INTEGER NOT NULL,
                step_index INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scene_id, pad_number, step_index)
            );
            """
        )
        conn.execute(
            "INSERT INTO controller_bindings (action, midi_type, number) VALUES ('kit_next', 'cc', 26)"
        )
        conn.commit()
        conn.close()

    def test_empty_old_kits_table_is_dropped_and_new_kits_table_works(self):
        storage.init_db()
        self.assertEqual(storage.list_scenes(), [])
        self.assertEqual(storage.list_kits(), [])
        kit_id = storage.create_kit("Teste", "01_electronic", 0, [])
        self.assertIsInstance(kit_id, int)

    def test_controller_binding_under_the_old_name_still_gets_fixed(self):
        storage.init_db()
        bindings = storage.list_controller_bindings()
        self.assertIn("scene_next", bindings)
        self.assertNotIn("kit_next", bindings)

    def test_running_init_db_twice_is_a_harmless_no_op(self):
        storage.init_db()
        storage.init_db()
        self.assertEqual(storage.list_scenes(), [])
        self.assertEqual(storage.list_kits(), [])


if __name__ == "__main__":
    unittest.main()

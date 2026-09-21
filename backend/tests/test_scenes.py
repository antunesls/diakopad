import tempfile
import unittest
from pathlib import Path

import storage


class SceneSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"
        storage.init_db()

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def _save_scene(self, name: str, state: dict | None = None, steps: list[dict] | None = None) -> int:
        return storage.save_scene(
            name,
            storage.list_pads(),
            storage.list_pad_effects(),
            storage.list_knob_mappings(),
            scene_state=state,
            sequencer_steps=steps,
        )

    def test_saved_scene_captures_global_state_and_steps_and_starts_active(self):
        state = {
            "sequencer_bpm": "137",
            "metronome_style": "digital",
            "metronome_signature": "3_4",
            "metronome_running": "1",
            "sequencer_running": "0",
        }
        steps = [{"pad_number": 1, "step_index": 0, "active": True}]
        scene_id = self._save_scene("Cena A", state=state, steps=steps)

        scene = storage.get_scene(scene_id)

        self.assertTrue(scene["active"])
        self.assertEqual(scene["state"]["sequencer_bpm"], "137")
        self.assertEqual(scene["state"]["metronome_signature"], "3_4")
        self.assertEqual(scene["state"]["metronome_running"], "1")
        self.assertEqual(len(scene["steps"]), 1)
        self.assertTrue(scene["steps"][0]["active"])

    def test_inactive_scene_is_excluded_from_active_list(self):
        active_id = self._save_scene("Ativa")
        inactive_id = self._save_scene("Inativa")
        storage.set_scene_active(inactive_id, False)

        active_ids = [s["id"] for s in storage.list_active_scenes()]

        self.assertIn(active_id, active_ids)
        self.assertNotIn(inactive_id, active_ids)

    def test_set_scene_active_toggles_the_flag(self):
        scene_id = self._save_scene("Alterna")

        self.assertTrue(storage.set_scene_active(scene_id, False))
        self.assertFalse(storage.get_scene(scene_id)["active"])
        self.assertTrue(storage.set_scene_active(scene_id, True))
        self.assertTrue(storage.get_scene(scene_id)["active"])

    def test_load_scene_restores_bpm_metronome_settings_and_steps(self):
        state = {
            "sequencer_bpm": "150",
            "metronome_style": "digital",
            "metronome_signature": "5_4",
            "metronome_running": "1",
            "sequencer_running": "1",
        }
        steps = [
            {"pad_number": 2, "step_index": 3, "active": True},
            {"pad_number": 4, "step_index": 7, "active": True},
        ]
        scene_id = self._save_scene("Cena B", state=state, steps=steps)
        storage.set_setting("sequencer_bpm", "90")
        storage.set_setting("metronome_signature", "4_4")
        storage.clear_sequencer_steps()

        loaded = storage.load_scene(scene_id)

        settings = storage.get_settings()
        self.assertEqual(loaded["state"]["metronome_running"], "1")
        self.assertEqual(settings["sequencer_bpm"], "150")
        self.assertEqual(settings["metronome_signature"], "5_4")
        active_steps = [s for s in storage.list_sequencer_steps() if s["active"]]
        self.assertEqual(
            {(s["pad_number"], s["step_index"]) for s in active_steps},
            {(2, 3), (4, 7)},
        )


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as diakopad_app
import storage


class KitApiTests(unittest.TestCase):
    """POST /api/kits, /api/kits/from-pads e /api/kits/{id}/load (usados pelo MCP)."""

    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"
        storage.init_db()
        self.kick = storage.add_sample("kick.wav", "Kick")
        self.snare = storage.add_sample("snare.wav", "Snare")
        env = patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)
        self.apply_changed = AsyncMock()
        orch = patch.object(diakopad_app.orchestrator, "apply_changed_pads", self.apply_changed)
        orch.start()
        self.addCleanup(orch.stop)
        self.client = TestClient(diakopad_app.app)

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def _create(self, **body):
        body.setdefault("name", "Meu Kit")
        body.setdefault("pads", [{"pad_number": 1, "sample_id": self.kick}])
        return self.client.post("/api/kits", json=body)

    def test_create_kit_fills_display_name_from_sample(self):
        res = self._create(category="custom", pads=[
            {"pad_number": 1, "sample_id": self.kick},
            {"pad_number": 2, "sample_id": self.snare, "display_name": "Caixa"},
        ])
        self.assertEqual(res.status_code, 200)
        kit = res.json()["kit"]
        self.assertEqual(kit["category"], "custom")
        self.assertEqual(
            {(p["pad_number"], p["display_name"]) for p in kit["pads"]},
            {(1, "Kick"), (2, "Caixa")},
        )

    def test_create_kit_rejects_unknown_sample_bad_pad_and_duplicates(self):
        self.assertEqual(self._create(pads=[{"pad_number": 1, "sample_id": 999}]).status_code, 404)
        self.assertEqual(self._create(pads=[{"pad_number": 17, "sample_id": self.kick}]).status_code, 400)
        dup = [{"pad_number": 1, "sample_id": self.kick}, {"pad_number": 1, "sample_id": self.snare}]
        self.assertEqual(self._create(pads=dup).status_code, 400)
        self.assertEqual(self._create(name="  ").status_code, 400)
        self.assertEqual(storage.list_kits(), [])

    def test_existing_name_needs_overwrite(self):
        self.assertEqual(self._create().status_code, 200)
        self.assertEqual(self._create(pads=[{"pad_number": 2, "sample_id": self.snare}]).status_code, 409)
        res = self._create(overwrite=True, pads=[{"pad_number": 2, "sample_id": self.snare}])
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p["pad_number"] for p in res.json()["kit"]["pads"]], [2])
        self.assertEqual(len(storage.list_kits()), 1)

    def test_from_pads_snapshots_only_assigned_pads(self):
        self.assertEqual(self.client.post("/api/kits/from-pads", json={"name": "Vazio"}).status_code, 400)
        storage.assign_sample(3, self.kick)
        storage.assign_sample(5, self.snare)
        res = self.client.post("/api/kits/from-pads", json={"name": "Ao vivo"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            [(p["pad_number"], p["sample_id"], p["display_name"]) for p in res.json()["kit"]["pads"]],
            [(3, self.kick, "Kick"), (5, self.snare, "Snare")],
        )

    def test_load_kit_assigns_pads_and_optionally_clears_the_rest(self):
        kit_id = self._create(pads=[{"pad_number": 1, "sample_id": self.kick}]).json()["kit"]["id"]
        storage.assign_sample(2, self.snare)

        res = self.client.post(f"/api/kits/{kit_id}/load", json={})
        self.assertEqual(res.status_code, 200)
        pads = {p["pad_number"]: p["sample_id"] for p in storage.list_pads()}
        self.assertEqual((pads[1], pads[2]), (self.kick, self.snare))
        self.apply_changed.assert_awaited_once()

        self.client.post(f"/api/kits/{kit_id}/load", json={"clear_other_pads": True})
        pads = {p["pad_number"]: p["sample_id"] for p in storage.list_pads()}
        self.assertEqual((pads[1], pads[2]), (self.kick, None))

    def test_load_unknown_kit_is_404(self):
        self.assertEqual(self.client.post("/api/kits/999/load", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

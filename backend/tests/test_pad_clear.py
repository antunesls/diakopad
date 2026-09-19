import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app as diakopad_app
import storage


class ClearAllPadsStorageTests(unittest.TestCase):
    def test_clear_all_pads_removes_every_sample_assignment(self):
        original_db_path = storage.DB_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            storage.DB_PATH = Path(temp_dir) / "test.db"
            try:
                storage.init_db()
                sample_id = storage.add_sample("kick.wav", "Kick")
                storage.assign_sample(1, sample_id)
                storage.assign_sample(2, sample_id)

                storage.clear_all_pads()

                self.assertTrue(all(pad["sample_id"] is None for pad in storage.list_pads()))
            finally:
                storage.DB_PATH = original_db_path


class ClearAllPadsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_all_pads_reapplies_the_engine_and_broadcasts(self):
        with (
            patch("app.storage.clear_all_pads") as clear_all,
            patch("app.orchestrator.apply_all_pads", new=AsyncMock()) as apply_all,
            patch("app._broadcast_pads", new=AsyncMock()) as broadcast,
        ):
            result = await diakopad_app.clear_all_pads()

        clear_all.assert_called_once()
        apply_all.assert_awaited_once()
        broadcast.assert_awaited_once()
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()

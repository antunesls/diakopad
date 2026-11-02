import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app as diakopad_app
import storage
from engine import orchestrator


class StartupResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "diakopad.db"

    async def asyncTearDown(self):
        if diakopad_app._engine_status_task is not None:
            diakopad_app._engine_status_task.cancel()
            diakopad_app._engine_status_task = None
        orchestrator.report_error("")
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    async def test_engine_failure_does_not_abort_startup(self):
        with (
            patch("app.midi.open_input"),
            patch("app.orchestrator.startup", new=AsyncMock(side_effect=RuntimeError("mod-host sumiu"))),
            patch("app.orchestrator.apply_all_pads", new=AsyncMock()) as apply_all,
        ):
            await diakopad_app.on_startup()

        apply_all.assert_not_awaited()
        self.assertIn("mod-host sumiu", orchestrator.engine_status()["last_error"])
        # O que não depende do motor foi carregado mesmo assim.
        self.assertEqual(len(storage.list_pads()), 16)
        self.assertIsNotNone(diakopad_app._engine_status_task)


if __name__ == "__main__":
    unittest.main()

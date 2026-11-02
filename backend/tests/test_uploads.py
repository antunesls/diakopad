import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile, WebSocketDisconnect
from fastapi.testclient import TestClient

import app as diakopad_app
import storage


class SaveUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_file_within_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / "ok.wav"
            await diakopad_app._save_upload(UploadFile(io.BytesIO(b"x" * 100), filename="ok.wav"), dest, 1000)
            self.assertEqual(dest.read_bytes(), b"x" * 100)

    async def test_oversized_upload_returns_413_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / "big.wav"
            upload = UploadFile(io.BytesIO(b"x" * (3 * 1024 * 1024)), filename="big.wav")
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app._save_upload(upload, dest, 1024 * 1024)
            self.assertEqual(ctx.exception.status_code, 413)
            self.assertFalse(dest.exists())


class BackupDbTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "diakopad.db"

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def test_no_database_yet_means_no_backup(self):
        self.assertIsNone(storage.backup_db())

    def test_backup_is_a_readable_copy(self):
        storage.init_db()
        storage.add_sample("kick.wav", "Kick")

        dest = storage.backup_db()

        conn = sqlite3.connect(dest)
        try:
            names = [row[0] for row in conn.execute("SELECT display_name FROM samples")]
        finally:
            conn.close()
        self.assertEqual(names, ["Kick"])

    def test_keeps_only_the_most_recent_backups(self):
        storage.init_db()
        backups_dir = storage.DB_PATH.parent / "backups"
        backups_dir.mkdir()
        for day in range(1, 6):
            (backups_dir / f"diakopad-2026010{day}-000000.db").write_bytes(b"old")

        storage.backup_db(keep=3)

        remaining = sorted(p.name for p in backups_dir.glob("diakopad-*.db"))
        self.assertEqual(len(remaining), 3)
        self.assertNotIn("diakopad-20260101-000000.db", remaining)
        self.assertNotIn("diakopad-20260103-000000.db", remaining)

    def test_init_db_enables_wal(self):
        storage.init_db()
        conn = storage.get_connection()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode, "wal")


class TokenAuthTests(unittest.TestCase):
    """TestClient se apresenta como host "testclient" - ou seja, um aparelho
    remoto, não o loopback do laptop."""

    def setUp(self):
        self.client = TestClient(diakopad_app.app)

    def test_without_token_configured_everything_is_open(self):
        with patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}):
            self.assertEqual(self.client.get("/").status_code, 200)

    def test_remote_client_without_cookie_is_rejected(self):
        with patch.dict(os.environ, {"DIAKOPAD_TOKEN": "segredo"}):
            self.assertEqual(self.client.get("/").status_code, 401)

    def test_wrong_query_token_is_rejected(self):
        with patch.dict(os.environ, {"DIAKOPAD_TOKEN": "segredo"}):
            self.assertEqual(self.client.get("/?token=errado").status_code, 401)

    def test_query_token_sets_cookie_then_plain_url_works(self):
        with patch.dict(os.environ, {"DIAKOPAD_TOKEN": "segredo"}):
            first = self.client.get("/?token=segredo", follow_redirects=False)
            self.assertEqual(first.status_code, 303)
            self.assertNotIn("token=", first.headers["location"])
            self.assertEqual(self.client.get("/").status_code, 200)

    def test_remote_websocket_without_cookie_is_closed(self):
        with patch.dict(os.environ, {"DIAKOPAD_TOKEN": "segredo"}):
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with self.client.websocket_connect("/ws") as ws:
                    ws.receive_json()
        self.assertEqual(ctx.exception.code, 1008)

    def test_loopback_is_always_allowed(self):
        with patch.dict(os.environ, {"DIAKOPAD_TOKEN": "segredo"}):
            self.assertTrue(diakopad_app._is_authorized("127.0.0.1", None))
            self.assertTrue(diakopad_app._is_authorized("::1", None))
            self.assertFalse(diakopad_app._is_authorized("192.168.0.20", None))
            self.assertFalse(diakopad_app._is_authorized("192.168.0.20", "errado"))
            self.assertTrue(diakopad_app._is_authorized("192.168.0.20", "segredo"))


if __name__ == "__main__":
    unittest.main()

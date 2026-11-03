"""Testes das rotas /api/smcpad com o simulador do aparelho."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as diakopad_app
import smcpad as sp
import smcpad_api
import smcpad_device as dev
from smcpad_sim import FakeSmcPad


class ApiCase(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeSmcPad()
        self.enabled = True
        self.service = dev.SmcPadService(lambda: self.fake, Path(self.tmp.name), lambda: self.enabled)
        smcpad_api.set_service(self.service)
        self.addCleanup(smcpad_api.set_service, None)
        backups = patch.object(smcpad_api, "BACKUP_DIR", Path(self.tmp.name))
        backups.start()
        self.addCleanup(backups.stop)
        self.client = TestClient(diakopad_app.app)

    NOTE = {"scope": "pad", "preset": 1, "index": 32, "field": "note", "value": 37}


class ReadApiTests(ApiCase):
    def test_status_connected(self):
        body = self.client.get("/api/smcpad/status").json()
        self.assertTrue(body["connected"])
        self.assertEqual(body["identity"], "SMC-PAD_004")
        self.assertEqual(body["globals"]["tempo"], 120)

    def test_status_when_device_is_missing_is_not_an_error(self):
        def missing():
            raise dev.DeviceError("SMC-PAD não encontrado")

        smcpad_api.set_service(dev.SmcPadService(missing, Path(self.tmp.name), lambda: False))
        response = self.client.get("/api/smcpad/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["connected"])
        self.assertIn("não encontrado", body["error"])
        self.assertFalse(body["write_enabled"])

    def test_config_has_everything_the_screen_needs(self):
        body = self.client.get("/api/smcpad/config").json()
        self.assertEqual((body["current_preset"], body["preset_number"]), (1, 1))
        self.assertEqual(len(body["preset"]["pads"]), 128)
        self.assertEqual(len(body["preset"]["knobs"]), 16)
        self.assertEqual(len(body["preset"]["buttons"]), 5)
        self.assertEqual(len(body["calibration"]), 16)
        self.assertEqual(body["preset"]["pads"][32]["color"], "#f000f0")
        self.assertEqual(body["enums"]["pad_types"][0], "Note")
        self.assertEqual(body["limits"]["pad"]["channel"], {"min": 1, "max": 16})
        self.assertEqual(body["limits"]["globals"]["tempo"], {"min": 20, "max": 300})

    def test_config_for_another_preset(self):
        body = self.client.get("/api/smcpad/config?preset=2").json()
        self.assertEqual((body["current_preset"], body["preset_number"]), (1, 2))
        self.assertEqual(self.client.get("/api/smcpad/config?preset=9").status_code, 422)

    def test_config_when_device_is_missing_is_503(self):
        self.fake.drop = 1000
        self.assertEqual(self.client.get("/api/smcpad/config").status_code, 503)


class WriteApiTests(ApiCase):
    def test_edit_is_forbidden_when_writing_is_disabled(self):
        self.enabled = False
        response = self.client.post("/api/smcpad/edits", json={"edits": [self.NOTE]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.fake.writes, [])

    def test_edit_applies_and_reports(self):
        response = self.client.post("/api/smcpad/edits", json={"edits": [self.NOTE]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"] and body["committed"])
        self.assertEqual(body["applied"][0]["before"], 36)
        self.assertEqual(self.client.get("/api/smcpad/config").json()["preset"]["pads"][32]["note"], 37)
        names = [b["name"] for b in self.client.get("/api/smcpad/backups").json()]
        self.assertEqual(len(names), 1)

    def test_invalid_edit_is_422_and_touches_nothing(self):
        response = self.client.post("/api/smcpad/edits", json={"edits": [{**self.NOTE, "value": 999}]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.fake.writes, [])

    def test_verify_failure_is_502_with_rollback(self):
        original = bytes(self.fake.regions[sp.REGION_CONFIG])
        self.fake.corrupt_next_write = True
        response = self.client.post("/api/smcpad/edits", json={"edits": [self.NOTE]})
        self.assertEqual(response.status_code, 502)
        self.assertIn("restaurados", response.json()["detail"])
        self.assertEqual(bytes(self.fake.regions[sp.REGION_CONFIG]), original)

    def test_write_mode_toggle_is_persisted(self):
        with patch.object(smcpad_api.storage, "set_setting") as set_setting:
            self.assertEqual(self.client.post("/api/smcpad/write-enabled", json={"enabled": True}).json(), {"write_enabled": True})
            set_setting.assert_called_with("smcpad_write_enabled", "1")
            self.client.post("/api/smcpad/write-enabled", json={"enabled": False})
            set_setting.assert_called_with("smcpad_write_enabled", "0")

    def test_write_flag_reads_the_setting(self):
        with patch.object(smcpad_api.storage, "get_settings", return_value={"smcpad_write_enabled": "1"}):
            self.assertTrue(smcpad_api._write_enabled())
        with patch.object(smcpad_api.storage, "get_settings", return_value={}):
            self.assertFalse(smcpad_api._write_enabled())


class FileApiTests(ApiCase):
    def test_export_download(self):
        response = self.client.get("/api/smcpad/preset/1/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.content), sp.PRESET_SIZE)
        self.assertIn("smcpad-preset1.spc", response.headers["content-disposition"])
        self.assertEqual(self.client.get("/api/smcpad/preset/9/export").status_code, 422)

    def test_import_roundtrip(self):
        data = bytearray(self.client.get("/api/smcpad/preset/1/export").content)
        data[0x415] = 99
        response = self.client.post("/api/smcpad/preset/1/import", content=bytes(data))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/smcpad/config").json()["preset"]["pads"][32]["note"], 99)

    def test_import_rejects_wrong_size_and_forbids_when_disabled(self):
        self.assertEqual(self.client.post("/api/smcpad/preset/1/import", content=b"abc").status_code, 422)
        self.assertEqual(self.client.post("/api/smcpad/preset/1/import", content=bytes(sp.PRESET_SIZE + 1)).status_code, 413)
        self.enabled = False
        self.assertEqual(self.client.post("/api/smcpad/preset/1/import", content=bytes(sp.PRESET_SIZE)).status_code, 403)

    def test_backups_empty_by_default(self):
        self.assertEqual(self.client.get("/api/smcpad/backups").json(), [])


if __name__ == "__main__":
    unittest.main()

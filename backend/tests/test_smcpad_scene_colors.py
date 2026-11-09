"""Cores do SMC-PAD por cena: armazenamento, leitura/aplicação no aparelho, ganchos de salvar/carregar cena."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as diakopad_app
import smcpad as sp
import smcpad_api
import smcpad_device as dev
import storage
from smcpad_sim import FakeSmcPad

PALETTE = [f"#{(i * 2) % 256:02x}{(i * 5) % 256:02x}{(i * 11) % 256:02x}" for i in range(128)]
PALETTE_2 = [f"#{(255 - i * 2) % 256:02x}{(i * 3) % 256:02x}{(i * 7) % 256:02x}" for i in range(128)]


def device_colors(fake: FakeSmcPad, preset: int = 1) -> list[str]:
    image = bytes(fake.regions[sp.REGION_CONFIG])
    return [p["color"] for p in sp.parse_preset(sp.preset_slice(image, preset))["pads"]]


def non_color_bytes(image: bytes) -> bytes:
    """A memória sem os 3 bytes de cor de cada pad (para provar que só a cor mudou)."""
    out = bytearray(image)
    for preset in range(sp.PRESET_COUNT):
        for i in range(sp.PAD_COUNT):
            off = preset * sp.PRESET_SIZE + sp.PAD_BASE + i * sp.PAD_SIZE + 5
            out[off : off + 3] = b"\x00\x00\x00"
    return bytes(out)


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        for p in (
            patch.object(storage, "DB_PATH", self.tmp / "t.db"),
            patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}),
            patch.object(smcpad_api, "BACKUP_DIR", self.tmp / "backups"),
        ):
            p.start()
            self.addCleanup(p.stop)
        storage.init_db()
        self.fake = FakeSmcPad()
        self.service = dev.SmcPadService(lambda: self.fake, self.tmp / "backups", lambda: False)  # gravação DESLIGADA de propósito
        smcpad_api.set_service(self.service)
        self.addCleanup(smcpad_api.set_service, None)
        diakopad_app._scene_colors_latest = None
        self.addCleanup(self._tmp.cleanup)

    def scene(self, name="A") -> int:
        return storage.save_scene(name, storage.list_pads(), storage.list_pad_effects())


class StorageTests(Base):
    async def test_roundtrip_normalizes_case(self):
        sid = self.scene()
        self.assertIsNone(storage.get_scene_smc_colors(sid))
        self.assertTrue(storage.set_scene_smc_colors(sid, [c.upper() for c in PALETTE]))
        self.assertEqual(storage.get_scene_smc_colors(sid), PALETTE)

    async def test_replace_delete_and_missing_scene(self):
        sid = self.scene()
        storage.set_scene_smc_colors(sid, PALETTE)
        storage.set_scene_smc_colors(sid, PALETTE_2)
        self.assertEqual(storage.get_scene_smc_colors(sid), PALETTE_2)
        self.assertTrue(storage.delete_scene_smc_colors(sid))
        self.assertFalse(storage.delete_scene_smc_colors(sid))
        self.assertFalse(storage.set_scene_smc_colors(9999, PALETTE))

    async def test_invalid_colors_are_rejected(self):
        sid = self.scene()
        for bad in (PALETTE[:127], PALETTE + ["#000000"], ["vermelho"] * 128, ["#12345"] * 128, [None] * 128, "x"):
            with self.subTest(bad=str(bad)[:20]), self.assertRaises(ValueError):
                storage.set_scene_smc_colors(sid, bad)
        self.assertIsNone(storage.get_scene_smc_colors(sid))

    async def test_list_scenes_flags_scenes_with_colors(self):
        a = self.scene("A")
        self.scene("B")
        storage.set_scene_smc_colors(a, PALETTE)
        flags = {s["name"]: bool(s["has_smc_colors"]) for s in storage.list_scenes()}
        self.assertEqual(flags, {"A": True, "B": False})

    async def test_resaving_a_scene_keeps_its_colors_and_deleting_it_removes_them(self):
        sid = self.scene("A")
        storage.set_scene_smc_colors(sid, PALETTE)
        self.assertEqual(self.scene("A"), sid)  # mesmo id, conteúdo trocado
        self.assertEqual(storage.get_scene_smc_colors(sid), PALETTE)
        storage.delete_scene(sid)
        self.assertIsNone(storage.get_scene_smc_colors(sid))

    async def test_setting_defaults_on(self):
        self.assertEqual(storage.get_settings()["smcpad_scene_colors"], "1")


class DeviceColorTests(Base):
    async def test_read_colors_of_the_active_preset(self):
        colors = self.service.read_pad_colors()
        self.assertEqual(len(colors), 128)
        self.assertEqual(colors[32], "#f000f0")
        self.fake.regions[sp.REGION_GLOBALS][10] = 1  # preset 2 ativo
        base = sp.PRESET_SIZE + sp.PAD_BASE + 32 * sp.PAD_SIZE + 5
        self.fake.regions[sp.REGION_CONFIG][base : base + 3] = b"\x01\x02\x03"
        self.assertEqual(self.service.read_pad_colors()[32], "#010203")

    async def test_apply_changes_only_color_bytes_without_commit_backup_or_write_flag(self):
        before = bytes(self.fake.regions[sp.REGION_CONFIG])
        expected_changed = sum(1 for a, b in zip(device_colors(self.fake), PALETTE) if a != b)
        self.assertGreater(expected_changed, 100)
        result = self.service.apply_pad_colors(PALETTE)
        self.assertEqual(device_colors(self.fake), PALETTE)
        self.assertEqual(non_color_bytes(bytes(self.fake.regions[sp.REGION_CONFIG])), non_color_bytes(before))
        self.assertEqual(self.fake.commits, [])  # nada gravado de vez
        self.assertFalse((self.tmp / "backups").exists())  # sem backup
        self.assertFalse(self.service.write_enabled())
        self.assertEqual(result, {"changed": expected_changed, "committed": False})

    async def test_apply_is_minimal(self):
        self.service.apply_pad_colors(PALETTE)
        self.fake.writes.clear()
        self.assertEqual(self.service.apply_pad_colors(PALETTE), {"changed": 0, "committed": False})
        self.assertEqual(self.fake.writes, [])  # já estava igual: não escreve nada
        one = list(PALETTE)
        one[40] = "#abcdef"
        self.assertEqual(self.service.apply_pad_colors(one)["changed"], 1)
        self.assertEqual(self.fake.writes, [(5, sp.PAD_BASE + 40 * sp.PAD_SIZE + 5, bytes.fromhex("abcdef"))])

    async def test_apply_targets_the_active_preset(self):
        self.fake.regions[sp.REGION_GLOBALS][10] = 3  # preset 4
        before_p1 = device_colors(self.fake, 1)
        self.service.apply_pad_colors(PALETTE)
        self.assertEqual(device_colors(self.fake, 4), PALETTE)
        self.assertEqual(device_colors(self.fake, 1), before_p1)

    async def test_verify_failure_restores_the_original_bytes(self):
        before = bytes(self.fake.regions[sp.REGION_CONFIG])
        self.fake.corrupt_next_write = True
        with self.assertRaises(dev.VerifyError):
            self.service.apply_pad_colors(PALETTE)
        self.assertEqual(bytes(self.fake.regions[sp.REGION_CONFIG]), before)
        self.assertEqual(self.fake.commits, [])

    async def test_validation_and_unreachable_device(self):
        with self.assertRaises(sp.EditError):
            self.service.apply_pad_colors(PALETTE[:10])
        with self.assertRaises(sp.EditError):
            self.service.apply_pad_colors(["#zzzzzz"] * 128)
        self.fake.drop = 1000
        with self.assertRaises(dev.DeviceError):
            self.service.read_pad_colors()
        self.assertEqual(self.fake.drop, 999)  # exatamente UMA tentativa: desiste logo, não trava a troca de cena


class SceneHookTests(Base):
    async def test_saving_a_scene_captures_the_device_colors(self):
        self.service.apply_pad_colors(PALETTE)
        result = await diakopad_app.save_scene(diakopad_app.SceneRequest(name="Louvor"))
        self.assertTrue(result["smc_colors"])
        self.assertEqual(storage.get_scene_smc_colors(result["scene"]["id"]), PALETTE)

    async def test_saving_without_the_device_still_saves_the_scene_and_keeps_old_colors(self):
        sid = self.scene("Louvor")
        storage.set_scene_smc_colors(sid, PALETTE)

        def missing():
            raise dev.DeviceError("SMC-PAD não encontrado")

        smcpad_api.set_service(dev.SmcPadService(missing, self.tmp / "b", lambda: False))
        result = await diakopad_app.save_scene(diakopad_app.SceneRequest(name="Louvor"))
        self.assertTrue(result["ok"])
        self.assertFalse(result["smc_colors"])
        self.assertEqual(storage.get_scene_smc_colors(sid), PALETTE)

    async def _load(self, scene_id):
        with (
            patch("app.orchestrator.apply_changed_pads", new=AsyncMock()),
            patch("app._broadcast_pads", new=AsyncMock()),
            patch("app._broadcast_pad_effects", new=AsyncMock()),
        ):
            result = await diakopad_app.load_scene(scene_id)
        await asyncio.gather(*list(diakopad_app._background_tasks))
        return result

    async def test_loading_a_scene_paints_the_device_without_committing(self):
        sid = self.scene("Louvor")
        storage.set_scene_smc_colors(sid, PALETTE)
        self.assertEqual((await self._load(sid))["ok"], True)
        self.assertEqual(device_colors(self.fake), PALETTE)
        self.assertEqual(self.fake.commits, [])

    async def test_scene_without_colors_leaves_the_device_alone_and_starts_no_task(self):
        sid = self.scene("Sem cores")
        before = bytes(self.fake.regions[sp.REGION_CONFIG])
        with (
            patch("app.orchestrator.apply_changed_pads", new=AsyncMock()),
            patch("app._broadcast_pads", new=AsyncMock()),
            patch("app._broadcast_pad_effects", new=AsyncMock()),
        ):
            await diakopad_app.load_scene(sid)
        self.assertEqual(len(diakopad_app._background_tasks), 0)
        self.assertEqual(bytes(self.fake.regions[sp.REGION_CONFIG]), before)
        self.assertEqual(self.fake.writes, [])

    async def test_a_dead_device_never_breaks_the_scene_load(self):
        sid = self.scene("Louvor")
        storage.set_scene_smc_colors(sid, PALETTE)
        self.fake.drop = 1000
        result = await self._load(sid)
        self.assertTrue(result["ok"])
        self.assertEqual(storage.get_settings()["current_scene_id"], str(sid))

    async def test_only_the_latest_scene_paints_when_switching_fast(self):
        a, b = self.scene("A"), self.scene("B")
        storage.set_scene_smc_colors(a, PALETTE)
        storage.set_scene_smc_colors(b, PALETTE_2)
        diakopad_app._schedule_scene_colors(a)
        diakopad_app._schedule_scene_colors(b)
        await asyncio.gather(*list(diakopad_app._background_tasks))
        self.assertEqual(device_colors(self.fake), PALETTE_2)

    async def test_the_setting_turns_the_feature_off(self):
        sid = self.scene("Louvor")
        storage.set_scene_smc_colors(sid, PALETTE)
        storage.set_setting("smcpad_scene_colors", "0")
        before = device_colors(self.fake)
        await self._load(sid)
        self.assertEqual(device_colors(self.fake), before)
        result = await diakopad_app.save_scene(diakopad_app.SceneRequest(name="Louvor"))
        self.assertFalse(result["smc_colors"])
        self.assertEqual(storage.get_scene_smc_colors(sid), PALETTE)  # nada foi recapturado


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        for p in (
            patch.object(storage, "DB_PATH", tmp / "t.db"),
            patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}),
            patch.object(smcpad_api, "BACKUP_DIR", tmp / "backups"),
        ):
            p.start()
            self.addCleanup(p.stop)
        storage.init_db()
        self.fake = FakeSmcPad()
        smcpad_api.set_service(dev.SmcPadService(lambda: self.fake, tmp / "backups", lambda: False))
        self.addCleanup(smcpad_api.set_service, None)
        self.addCleanup(self._tmp.cleanup)
        self.client = TestClient(diakopad_app.app)
        self.sid = storage.save_scene("Louvor", storage.list_pads(), storage.list_pad_effects())

    def test_list_and_toggle(self):
        body = self.client.get("/api/smcpad/scene-colors").json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["scenes"], [{"id": self.sid, "name": "Louvor", "has_colors": False}])
        self.assertEqual(self.client.post("/api/smcpad/scene-colors/enabled", json={"enabled": False}).json(), {"enabled": False})
        self.assertFalse(self.client.get("/api/smcpad/scene-colors").json()["enabled"])

    def test_capture_get_apply_delete(self):
        self.assertEqual(self.client.get(f"/api/smcpad/scenes/{self.sid}/colors").status_code, 404)
        self.assertEqual(self.client.post(f"/api/smcpad/scenes/{self.sid}/colors/apply").status_code, 404)
        self.assertEqual(self.client.post(f"/api/smcpad/scenes/{self.sid}/colors/capture").status_code, 200)
        saved = self.client.get(f"/api/smcpad/scenes/{self.sid}/colors").json()
        self.assertEqual(len(saved["colors"]), 128)
        self.assertEqual(saved["colors"][32], "#f000f0")
        self.assertTrue(self.client.get("/api/smcpad/scene-colors").json()["scenes"][0]["has_colors"])

        storage.set_scene_smc_colors(self.sid, PALETTE)
        response = self.client.post(f"/api/smcpad/scenes/{self.sid}/colors/apply")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["committed"], False)
        self.assertEqual(device_colors(self.fake), PALETTE)

        self.assertEqual(self.client.delete(f"/api/smcpad/scenes/{self.sid}/colors").json(), {"ok": True})
        self.assertEqual(self.client.get(f"/api/smcpad/scenes/{self.sid}/colors").status_code, 404)

    def test_unknown_scene_and_missing_device(self):
        self.assertEqual(self.client.post("/api/smcpad/scenes/999/colors/capture").status_code, 404)
        self.assertEqual(self.client.delete("/api/smcpad/scenes/999/colors").status_code, 404)
        self.fake.drop = 1000
        self.assertEqual(self.client.post(f"/api/smcpad/scenes/{self.sid}/colors/capture").status_code, 503)
        storage.set_scene_smc_colors(self.sid, PALETTE)
        self.assertEqual(self.client.post(f"/api/smcpad/scenes/{self.sid}/colors/apply").status_code, 503)


if __name__ == "__main__":
    unittest.main()

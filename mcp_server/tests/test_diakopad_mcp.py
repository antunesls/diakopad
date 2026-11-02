"""Testa as tools do MCP contra o app FastAPI real (via ASGI, sem rede),
com banco temporário e o motor de áudio mockado.

    cd mcp_server && python -m unittest discover tests
(o ambiente precisa de requirements.txt daqui e de backend/requirements.txt)
"""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "mcp_server"))

import app as diakopad_app  # noqa: E402
import diakopad_mcp  # noqa: E402
import kit_import  # noqa: E402
import storage  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


class McpToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.samples_dir = tmp / "samples"
        self.samples_dir.mkdir()
        patches = [
            patch.object(storage, "DB_PATH", tmp / "test.db"),
            patch.object(diakopad_app, "SAMPLES_DIR", self.samples_dir),
            patch.object(kit_import, "SAMPLES_DIR", self.samples_dir),
            patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}),
        ]
        for name in ("apply_pad", "apply_all_pads", "apply_changed_pads", "apply_master"):
            patches.append(patch.object(diakopad_app.orchestrator, name, AsyncMock(return_value=True)))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        storage.init_db()
        # ASGITransport usa 127.0.0.1 como cliente: passa pelo loopback sem token.
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=diakopad_app.app), base_url="http://test")
        diakopad_mcp.set_http_client(client)
        self.addAsyncCleanup(client.aclose)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def call(self, tool, **args):
        result = await diakopad_mcp.mcp.call_tool(tool, args)
        if result.is_error:
            raise ToolError(result.content[0].text)
        if result.structured_content is not None:
            return result.structured_content.get("result", result.structured_content)
        texts = [c.text for c in result.content]
        values = [json.loads(t) for t in texts]
        return values[0] if len(values) == 1 else values

    def _wav(self, name: str) -> Path:
        path = Path(self._tmp.name) / name
        path.write_bytes(b"RIFF....WAVEfmt ")
        return path

    async def test_upload_create_and_load_kit(self):
        kick = await self.call("upload_sound", source=str(self._wav("kick.wav")), folder="Drums")
        snare = await self.call("upload_sound", source=str(self._wav("x.wav")), display_name="Caixa")
        self.assertEqual(snare["display_name"], "Caixa")

        sounds = (await self.call("list_sounds", search="caix"))["sounds"]
        self.assertEqual([s["id"] for s in sounds], [snare["id"]])

        created = await self.call("create_kit", name="Rock", category="custom", pads=[
            {"pad_number": 1, "sample_id": kick["id"]},
            {"pad_number": 2, "sample_id": snare["id"]},
        ])
        kit_id = created["kit"]["id"]
        with self.assertRaisesRegex(ToolError, "409"):
            await self.call("create_kit", name="Rock", pads=[{"pad_number": 1, "sample_id": kick["id"]}])

        await self.call("load_kit", kit_id=kit_id)
        pads = {p["pad_number"]: p["sample_id"] for p in await self.call("list_pads")}
        self.assertEqual((pads[1], pads[2]), (kick["id"], snare["id"]))

        snapshot = await self.call("create_kit_from_pads", name="Snapshot")
        self.assertEqual(len(snapshot["kit"]["pads"]), 2)
        self.assertEqual({k["name"] for k in await self.call("list_kits")}, {"Rock", "Snapshot"})

    async def test_create_kit_from_files_builds_and_imports_a_pack(self):
        result = await self.call("create_kit_from_files", name="Novo", category="lab", files=[
            {"pad_number": 1, "source": str(self._wav("bumbo.wav"))},
            {"pad_number": 4, "source": str(self._wav("prato.wav")), "display_name": "Ride"},
        ])
        kit = result["kit"]
        self.assertEqual((kit["name"], kit["category"]), ("Novo", "lab"))
        self.assertEqual([(p["pad_number"], p["display_name"]) for p in kit["pads"]], [(1, "bumbo"), (4, "Ride")])
        self.assertEqual(len(list(self.samples_dir.iterdir())), 2)

    async def test_import_kit_pack_from_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.wav", b"RIFF")
            zf.writestr("manifest.json", json.dumps({"kits": [{"name": "Pack", "pads": [{"pad_number": 1, "filename": "a.wav"}]}]}))
        path = Path(self._tmp.name) / "pack.zip"
        path.write_bytes(buf.getvalue())
        result = await self.call("import_kit_pack", source=str(path))
        self.assertEqual(len(result["kit_ids"]), 1)

    async def test_upload_from_url(self):
        async def fake_stream_get(request):
            return httpx.Response(200, content=b"RIFF....")

        real_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(fake_stream_get)
            return real_client(*args, **kwargs)

        with patch.object(diakopad_mcp.httpx, "AsyncClient", client_factory):
            sample = await self.call("upload_sound", source="https://example.com/sons/Hi%20Hat.wav")
        self.assertEqual(sample["display_name"], "Hi Hat")

    async def test_bad_inputs_become_tool_errors(self):
        with self.assertRaises(ToolError):
            await self.call("upload_sound", source=str(Path(self._tmp.name) / "nao-existe.wav"))
        with self.assertRaisesRegex(ToolError, "não suportado"):
            await self.call("upload_sound", source=str(self._write("notas.txt")))
        with self.assertRaises(ToolError):  # validação de schema (pad 17)
            await self.call("set_pad_mix", pad_number=17, volume_db=0)
        with self.assertRaises(ToolError):
            await self.call("set_pad_mix", pad_number=1, volume_db=40)
        with self.assertRaisesRegex(ToolError, "404"):
            await self.call("assign_pad_sound", pad_number=1, sample_id=999)
        with self.assertRaisesRegex(ToolError, "all_mappings"):
            await self.call("delete_knob_mapping")

    def _write(self, name: str) -> Path:
        path = Path(self._tmp.name) / name
        path.write_text("x")
        return path

    async def test_mix_master_settings_tempo_and_sequencer(self):
        await self.call("set_pad_mix", pad_number=3, volume_db=-6, pan=-50, tone=80)
        pad3 = next(p for p in await self.call("list_pads") if p["pad_number"] == 3)
        self.assertEqual((pad3["volume_db"], pad3["pan"]), (-6, -50))

        await self.call("set_master", volume=70, limiter_threshold_db=-3)
        self.assertEqual(float(storage.get_settings()["master_volume"]), 70)
        diakopad_app.orchestrator.apply_master.assert_awaited_with(70, False, True, -3)

        settings = await self.call("update_settings", sustain_mode=True, looper_duplicate_hit_window_ms=50)
        self.assertTrue(settings["sustain_mode"])

        await self.call("set_tempo", bpm=128)
        self.assertEqual((await self.call("get_overview"))["tempo"]["bpm"], 128)

        seq = await self.call("set_sequencer_pattern", steps={"1": [0, 8], "2": [4, 12]})
        self.assertIn("steps", seq)

        await self.call("save_scene", name="Abertura")
        self.assertEqual([s["name"] for s in await self.call("list_scenes")], ["Abertura"])


if __name__ == "__main__":
    unittest.main()

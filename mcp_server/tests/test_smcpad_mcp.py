"""Testa as tools smcpad_* do MCP contra o app FastAPI real (via ASGI) e o simulador do SMC-PAD.

    cd mcp_server && python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "mcp_server"))

import app as diakopad_app  # noqa: E402
import diakopad_mcp  # noqa: E402
import smcpad as sp  # noqa: E402
import smcpad_api  # noqa: E402
import smcpad_device as dev  # noqa: E402
from smcpad_sim import FakeSmcPad  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


class SmcPadMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fake = FakeSmcPad()
        self.enabled = False
        service = dev.SmcPadService(lambda: self.fake, self.tmp / "backups", lambda: self.enabled)
        patches = [
            patch.object(smcpad_api, "BACKUP_DIR", self.tmp / "backups"),
            patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        smcpad_api.set_service(service)
        self.addCleanup(smcpad_api.set_service, None)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=diakopad_app.app), base_url="http://test")
        diakopad_mcp.set_http_client(client)
        self.addAsyncCleanup(client.aclose)
        self.addCleanup(self._tmp.cleanup)

    async def call(self, tool, **args):
        result = await diakopad_mcp.mcp.call_tool(tool, args)
        if result.is_error:
            raise ToolError(result.content[0].text)
        if result.structured_content is not None:
            return result.structured_content.get("result", result.structured_content)
        values = [json.loads(c.text) for c in result.content]
        return values[0] if len(values) == 1 else values

    NOTE = {"scope": "pad", "preset": 1, "index": 32, "field": "note", "value": 37}

    async def test_tools_are_registered_with_the_right_safety_annotations(self):
        tools = {t.name: t for t in await diakopad_mcp.mcp.list_tools()}
        names = {"smcpad_status", "smcpad_get_config", "smcpad_edit", "smcpad_list_backups", "smcpad_export_preset", "smcpad_import_preset"}
        self.assertTrue(names <= set(tools))
        self.assertTrue(tools["smcpad_edit"].annotations.destructive_hint)
        self.assertTrue(tools["smcpad_import_preset"].annotations.destructive_hint)
        self.assertTrue(tools["smcpad_get_config"].annotations.read_only_hint)
        self.assertNotIn("smcpad_set_write_mode", tools)  # o agente não liga a gravação sozinho

    async def test_status(self):
        status = await self.call("smcpad_status")
        self.assertTrue(status["connected"])
        self.assertEqual(status["identity"], "SMC-PAD_004")
        self.assertFalse(status["write_enabled"])

    async def test_get_config_sections(self):
        summary = await self.call("smcpad_get_config")
        self.assertEqual((summary["current_preset"], summary["preset_number"]), (1, 1))
        self.assertEqual(summary["globals"]["tempo"], 120)
        self.assertNotIn("raw", summary["globals"])
        self.assertIn("pad_types", summary["enums"])
        self.assertIn("limits", summary)
        self.assertNotIn("pads", summary)

        pads = (await self.call("smcpad_get_config", section="pads", group=3))["pads"]
        self.assertEqual(len(pads), 16)
        self.assertEqual((pads[0]["index"], pads[0]["note"], pads[0]["color"]), (32, 36, "#f000f0"))
        self.assertNotIn("extra", pads[0])  # só aparece quando há conteúdo

        self.assertEqual(len((await self.call("smcpad_get_config", section="buttons"))["buttons"]), 5)
        self.assertEqual(len((await self.call("smcpad_get_config", section="knobs"))["knobs"]), 16)
        self.assertEqual(len((await self.call("smcpad_get_config", section="calibration"))["calibration"]), 16)
        self.assertEqual(len((await self.call("smcpad_get_config", section="all"))["pads"]), 128)
        other = await self.call("smcpad_get_config", preset=2, section="globals")
        self.assertEqual((other["current_preset"], other["preset_number"]), (1, 2))
        with self.assertRaises(ToolError):
            await self.call("smcpad_get_config", preset=9)

    async def test_edit_is_refused_until_the_user_enables_writing(self):
        with self.assertRaisesRegex(ToolError, "403"):
            await self.call("smcpad_edit", edits=[self.NOTE])
        self.assertEqual(self.fake.writes, [])

    async def test_edit_pad_color_and_note_then_read_back(self):
        self.enabled = True
        result = await self.call("smcpad_edit", edits=[
            {**self.NOTE, "expected": 36},
            {"scope": "pad", "preset": 1, "index": 32, "field": "color", "value": "#FF0000"},
            {"scope": "pad", "preset": 1, "index": 32, "field": "led", "value": 100},
        ])
        self.assertTrue(result["ok"] and result["committed"])
        self.assertEqual(len(result["applied"]), 3)
        pad = (await self.call("smcpad_get_config", section="pads", group=3))["pads"][0]
        self.assertEqual((pad["note"], pad["color"], pad["led"]), (37, "#ff0000", 100))
        self.assertEqual(len(await self.call("smcpad_list_backups")), 1)

    async def test_edit_other_sections(self):
        self.enabled = True
        await self.call("smcpad_edit", edits=[
            {"scope": "globals", "field": "tempo", "value": 90},
            {"scope": "button", "preset": 1, "index": 0, "field": "sysex", "value": "F0 7F 7F 06 02 F7"},
            {"scope": "knob", "preset": 1, "index": 0, "field": "cc", "value": 50},
            {"scope": "calibration", "pad": 1, "field": "level", "value": 3},
        ])
        self.assertEqual((await self.call("smcpad_get_config", section="globals"))["globals"]["tempo"], 90)
        self.assertEqual((await self.call("smcpad_get_config", section="buttons"))["buttons"][0]["sysex"], "F0 7F 7F 06 02 F7")
        self.assertEqual((await self.call("smcpad_get_config", section="knobs"))["knobs"][0]["cc"], 50)
        self.assertEqual((await self.call("smcpad_get_config", section="calibration"))["calibration"][0]["raw"], 250)

    async def test_invalid_and_stale_edits_become_tool_errors(self):
        self.enabled = True
        with self.assertRaises(ToolError):  # esquema: índice de pad fora de 0-127
            await self.call("smcpad_edit", edits=[{**self.NOTE, "index": 200}])
        with self.assertRaises(ToolError):  # esquema: escopo desconhecido
            await self.call("smcpad_edit", edits=[{"scope": "nada", "field": "x", "value": 1}])
        with self.assertRaisesRegex(ToolError, "422"):  # valor fora da faixa (validado no servidor)
            await self.call("smcpad_edit", edits=[{**self.NOTE, "value": 500}])
        with self.assertRaisesRegex(ToolError, "422"):  # valor esperado diferente do atual
            await self.call("smcpad_edit", edits=[{**self.NOTE, "expected": 99}])
        with self.assertRaisesRegex(ToolError, "422"):  # preset que não está ativo
            await self.call("smcpad_edit", edits=[{**self.NOTE, "preset": 2}])
        with self.assertRaises(ToolError):  # lista vazia
            await self.call("smcpad_edit", edits=[])
        self.assertEqual(self.fake.writes, [])

    async def test_export_and_import_preset_files(self):
        out = self.tmp / "p1.spc"
        info = await self.call("smcpad_export_preset", preset=1, destination=str(out))
        self.assertEqual((info["bytes"], out.stat().st_size), (sp.PRESET_SIZE, sp.PRESET_SIZE))
        with self.assertRaisesRegex(ToolError, ".spc"):
            await self.call("smcpad_export_preset", preset=1, destination=str(self.tmp / "p1.txt"))

        data = bytearray(out.read_bytes())
        data[0x415] = 99
        edited = self.tmp / "edit.spc"
        edited.write_bytes(bytes(data))
        with self.assertRaisesRegex(ToolError, "403"):  # gravação desligada
            await self.call("smcpad_import_preset", preset=1, source=str(edited))
        self.enabled = True
        result = await self.call("smcpad_import_preset", preset=1, source=str(edited))
        self.assertTrue(result["ok"])
        self.assertEqual((await self.call("smcpad_get_config", section="pads", group=3))["pads"][0]["note"], 99)
        with self.assertRaisesRegex(ToolError, "não encontrado"):
            await self.call("smcpad_import_preset", preset=1, source=str(self.tmp / "nao-existe.spc"))

    async def test_missing_device_is_reported_not_raised_by_status(self):
        def missing():
            raise dev.DeviceError("SMC-PAD não encontrado")

        smcpad_api.set_service(dev.SmcPadService(missing, self.tmp / "b", lambda: False))
        status = await self.call("smcpad_status")
        self.assertFalse(status["connected"])
        with self.assertRaisesRegex(ToolError, "503"):
            await self.call("smcpad_get_config")


if __name__ == "__main__":
    unittest.main()

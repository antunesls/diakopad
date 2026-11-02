"""Hydrogen kit import, per-pad tune and the master compressor/limiter."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as diakopad_app
import hydrogen_kits
import sfz
import storage
from engine import effects_catalog, orchestrator

KIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<drumkit_info xmlns="http://www.hydrogen-music.org/drumkit">
 <name>Test Pearl 1.0</name>
 <author>Fulano</author>
 <license>GPL</license>
 <instrumentList>
  {instruments}
 </instrumentList>
</drumkit_info>
"""


def instrument(name, layers):
    body = "".join(
        f"<layer><filename>{fn}</filename><min>{lo}</min><max>{hi}</max></layer>" for fn, lo, hi in layers
    )
    return f"<instrument><name>{name}</name><instrumentComponent>{body}</instrumentComponent></instrument>"


class HydrogenKitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.kits = tmp / "drumkits"
        self.samples = tmp / "samples"
        self.samples.mkdir()
        for p in (
            patch.object(hydrogen_kits, "KIT_DIRS", [self.kits]),
            patch.object(hydrogen_kits, "SAMPLES_DIR", self.samples),
            patch.object(storage, "DB_PATH", tmp / "t.db"),
        ):
            p.start()
            self.addCleanup(p.stop)
        storage.init_db()

    def _kit(self, folder, names):
        path = self.kits / folder
        path.mkdir(parents=True)
        insts = []
        for name in names:
            stem = name.lower().replace(" ", "_")
            layers = [(f"{stem}_soft.wav", 0.0, 0.5), (f"{stem}_hard.wav", 0.5, 1.0)]
            for fn, _, _ in layers:
                (path / fn).write_bytes(b"RIFF")
            insts.append(instrument(name, layers))
        (path / "drumkit.xml").write_text(KIT_XML.format(instruments="".join(insts)), encoding="utf-8")
        return path

    def test_firm_layer_is_picked_and_roles_follow_the_diakopad_layout(self):
        names = ["Pearl Kick", "Pearl Side Stick", "Pearl Snare", "Pearl Snare Rimshot", "Pearl Tom Floor",
                 "Sabian Hat Closed", "Pearl Tom 2", "Sabian Hat Pedal", "Pearl Tom 1", "Sabian Hat Open",
                 "Sabian Hat Semi-Open", "Cowbell", "Paiste Ride", "Paiste Bell", "Sabian Crash", "Zildjian Splash"]
        kit = hydrogen_kits.parse_kit(self._kit("Pearl", names))
        self.assertTrue(all(i["file"].name.endswith("_hard.wav") for i in kit["instruments"]))
        pads = {n: i["name"] for n, i in hydrogen_kits.assign_pads(kit["instruments"]).items()}
        self.assertEqual(pads[1], "Pearl Kick")
        self.assertEqual(pads[2], "Pearl Snare")
        self.assertEqual(pads[3], "Pearl Side Stick")
        self.assertEqual(pads[4], "Sabian Hat Closed")
        self.assertEqual(pads[5], "Sabian Hat Open")
        self.assertEqual(pads[6], "Pearl Snare Rimshot")
        self.assertEqual(pads[7], "Sabian Hat Pedal")  # semi-open never steals the open hat (pad 5)
        self.assertEqual(pads[11], "Pearl Tom 1")
        self.assertEqual(pads[12], "Pearl Tom 2")
        self.assertEqual(pads[13], "Pearl Tom Floor")
        self.assertEqual(pads[14], "Sabian Crash")
        self.assertEqual(pads[15], "Paiste Ride")
        self.assertEqual(pads[16], "Paiste Bell")
        self.assertEqual(pads[8], "Zildjian Splash")
        self.assertEqual(pads[10], "Cowbell")

    def test_percussion_kit_without_drum_names_fills_pads_in_order(self):
        names = ["Cajon Thump", "Cajon Slap", "Maraca", "Hand Clap", "Finger Snaps", "Bongo Hi"]
        kit = hydrogen_kits.parse_kit(self._kit("Hand", names))
        pads = {n: i["name"] for n, i in hydrogen_kits.assign_pads(kit["instruments"]).items()}
        self.assertEqual(pads[1], "Cajon Thump")
        self.assertEqual(pads[2], "Cajon Slap")
        self.assertEqual(pads[3], "Hand Clap")
        self.assertEqual(pads[9], "Maraca")
        self.assertEqual(set(pads.values()), set(names))

    def test_import_creates_kit_with_credit_and_reimport_reuses_samples(self):
        self._kit("Pearl", ["Pearl Kick", "Pearl Snare"])
        listed = hydrogen_kits.list_kits()
        self.assertEqual([(k["name"], k["license"]) for k in listed], [("Pearl", "GPL")])
        kit_id = hydrogen_kits.import_kit("Pearl")
        kit = storage.get_kit(kit_id)
        self.assertEqual(kit["name"], "Test Pearl (Hydrogen)")
        self.assertEqual(kit["category"], hydrogen_kits.CATEGORY)
        self.assertIn("Fulano", kit["source_path"])
        self.assertEqual([p["pad_number"] for p in kit["pads"]], [1, 2])
        n_samples = len(storage.list_samples())
        self.assertEqual(hydrogen_kits.import_kit("Pearl"), kit_id)
        self.assertEqual(len(storage.list_samples()), n_samples)
        with self.assertRaises(FileNotFoundError):
            hydrogen_kits.import_kit("Nope")


class TuneTests(unittest.TestCase):
    def test_tune_becomes_transpose_plus_cents(self):
        v = {"filename": "snare.wav", "volume_db": 0, "pan": 0, "cutoff_hz": None, "tune": -2.5}
        text = sfz.render_pad_bank(2, 37, [v], {})
        self.assertIn("transpose=-2", text)
        self.assertIn("tune=-50", text)
        self.assertNotIn("transpose", sfz.render_pad_bank(2, 37, [{**v, "tune": 0}], {}))

    def test_tune_is_part_of_the_variant_identity(self):
        a = {"sample_id": 1, "filename": "s.wav", "volume_db": 0, "pan": 0, "cutoff_hz": None, "tune": 0}
        b = {**a, "tune": -3}
        self.assertNotEqual(orchestrator._variant_key(orchestrator._variant(a)),
                            orchestrator._variant_key(orchestrator._variant(b)))

    def test_mix_endpoint_validates_and_stores_tune(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(storage, "DB_PATH", Path(tmp) / "t.db"), \
                patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}), \
                patch.object(orchestrator, "apply_pad", AsyncMock(return_value=True)):
            storage.init_db()
            client = TestClient(diakopad_app.app)
            self.assertEqual(client.post("/api/pads/2/mix", json={"tune": 13}).status_code, 400)
            self.assertEqual(client.post("/api/pads/2/mix", json={"tune": -3}).status_code, 200)
            self.assertEqual(next(p for p in storage.list_pads() if p["pad_number"] == 2)["tune"], -3)


class MasterChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_limiter_lookahead_is_lowered_and_compressor_feeds_the_limiter(self):
        param_set = AsyncMock(return_value=True)
        with (
            patch.object(orchestrator, "_master_available", False),
            patch.object(orchestrator, "_master_limiter_available", False),
            patch.object(orchestrator, "_master_compressor_available", False),
            patch("engine.orchestrator.effects_catalog.master_gain_config", return_value={
                "lv2_uri": "urn:gain", "in_ports": ("in_l", "in_r"), "out_ports": ("out_l", "out_r"),
                "symbol": "trim", "min": -20.0, "max": 0.0}),
            patch("engine.orchestrator.modhost_client.add", AsyncMock(return_value=True)) as add,
            patch("engine.orchestrator.modhost_client.param_set", param_set),
            patch("engine.orchestrator.jackgraph.wait_for_port_async", AsyncMock(return_value=True)),
            patch("engine.orchestrator._wire_master_output"),
            patch("engine.orchestrator.rewire_audio_routes", AsyncMock()),
            patch("engine.orchestrator.storage.list_pad_effects", return_value=[]),
            patch("engine.orchestrator._compressor_settings", return_value={
                "enabled": True, "threshold_db": -12.0, "ratio": 2.0, "makeup_db": 2.0}),
        ):
            await orchestrator.apply_master(80, False, True, -3)
            limiter = effects_catalog.master_limiter_config()
            param_set.assert_any_await(orchestrator.MASTER_LIMITER_INSTANCE, "lk", limiter["lookahead_ms"])
            self.assertLessEqual(limiter["lookahead_ms"], 1.0)
            param_set.assert_any_await(orchestrator.MASTER_COMPRESSOR_INSTANCE, "sla", 0.0)
            param_set.assert_any_await(orchestrator.MASTER_COMPRESSOR_INSTANCE, "cr", 2.0)
            self.assertIn(orchestrator.MASTER_COMPRESSOR_INSTANCE, [c.args[1] for c in add.await_args_list])
            dest = orchestrator._master_destinations()
            self.assertTrue(dest[0].startswith(orchestrator.MASTER_COMPRESSOR_CLIENT + ":"))
            self.assertTrue(orchestrator.master_state()["compressor_enabled"])

            # turning the compressor off only flips its switch: no new plugin
            add.reset_mock()
            with patch("engine.orchestrator._compressor_settings", return_value={
                    "enabled": False, "threshold_db": -12.0, "ratio": 2.0, "makeup_db": 2.0}):
                await orchestrator.apply_master(80, False, True, -3)
            add.assert_not_awaited()
            param_set.assert_any_await(orchestrator.MASTER_COMPRESSOR_INSTANCE, "enabled", 0.0)
            self.assertFalse(orchestrator.master_state()["compressor_enabled"])


if __name__ == "__main__":
    unittest.main()

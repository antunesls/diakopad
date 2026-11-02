"""Shared FX buses, per-pad sound banks (instant scene switch) and prepared
sfizz swaps (sound changes without a gap)."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from types import SimpleNamespace
from fastapi.testclient import TestClient

import app as diakopad_app
import midi
import sfz
import storage
from engine import fx_buses, orchestrator, sfizz_proc


class FakeProc:
    _next_pid = 1000

    def __init__(self):
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.alive = True
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.alive = False
        self.killed = True

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class SfizzGenerationTests(unittest.TestCase):
    def setUp(self):
        for name in ("_procs", "_sfz_paths", "_jack_names", "_generation", "_prepared"):
            p = patch.object(sfizz_proc, name, {})
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(sfizz_proc, "_retired", [])
        p.start()
        self.addCleanup(p.stop)
        self.spawned = []

        def fake_popen(name, sfz_path):
            proc = FakeProc()
            self.spawned.append((name, proc))
            return proc

        for target, value in (("_popen", fake_popen), ("_binary_available", lambda: True)):
            p = patch.object(sfizz_proc, target, side_effect=value)
            p.start()
            self.addCleanup(p.stop)

    def test_prepare_runs_next_generation_alongside_current_and_commit_retires_old(self):
        client = sfizz_proc.client_name(3)
        first = sfizz_proc.prepare(client, Path("a.sfz"))
        self.assertTrue(sfizz_proc.commit(client))
        self.assertEqual(sfizz_proc.jack_name(client), first)

        second = sfizz_proc.prepare(client, Path("b.sfz"))
        self.assertNotEqual(first, second)
        # still playing the first one until commit
        self.assertEqual(sfizz_proc.jack_name(client), first)
        sfizz_proc.commit(client, grace=5)
        self.assertEqual(sfizz_proc.jack_name(client), second)
        self.assertEqual(sfizz_proc.retired_names(client), [first])
        old_proc = self.spawned[0][1]
        self.assertTrue(old_proc.alive, "old generation must keep ringing during the grace period")

    def test_reap_kills_retired_generation_only_after_grace_and_deletes_private_bank(self):
        client = sfizz_proc.client_name(1)
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live"
            live.mkdir()
            bank = live / "pad01_1.sfz"
            bank.write_text("x")
            sfizz_proc.prepare(client, bank)
            sfizz_proc.commit(client)
            sfizz_proc.prepare(client, live / "pad01_2.sfz")
            sfizz_proc.commit(client, grace=10)
            old_proc = self.spawned[0][1]
            deadline = sfizz_proc._retired[0]["deadline"]

            self.assertEqual(sfizz_proc.reap_retired(now=deadline - 1), 0)
            self.assertTrue(old_proc.alive)
            self.assertEqual(sfizz_proc.reap_retired(now=deadline + 0.1), 1)
            self.assertTrue(old_proc.killed)
            self.assertFalse(bank.exists())

    def test_abort_drops_the_prepared_generation_and_keeps_current(self):
        client = sfizz_proc.client_name(2)
        current = sfizz_proc.prepare(client, Path("a.sfz"))
        sfizz_proc.commit(client)
        sfizz_proc.prepare(client, Path("b.sfz"))
        sfizz_proc.abort(client)
        self.assertTrue(self.spawned[1][1].killed)
        self.assertEqual(sfizz_proc.jack_name(client), current)
        self.assertFalse(sfizz_proc.commit(client))

    def test_emergency_stop_kills_current_prepared_and_retired(self):
        client = sfizz_proc.client_name(4)
        sfizz_proc.prepare(client, Path("a.sfz"))
        sfizz_proc.commit(client)
        sfizz_proc.prepare(client, Path("b.sfz"))
        sfizz_proc.commit(client)
        sfizz_proc.prepare(client, Path("c.sfz"))
        sfizz_proc.emergency_stop_all()
        self.assertTrue(all(proc.killed or not proc.alive for _, proc in self.spawned))
        self.assertEqual(sfizz_proc._retired, [])

    def test_sfizz_is_started_with_full_preload_and_voice_cap(self):
        cmd = _real_popen_command("diakopad_pad01_1", Path("x.sfz"))
        self.assertIn(f"--preload_size={sfizz_proc.PRELOAD_SIZE}", cmd)
        self.assertIn(f"--num_voices={sfizz_proc.NUM_VOICES}", cmd)
        self.assertIn("--client_name=diakopad_pad01_1", cmd)


def _real_popen_command(name, sfz_path):
    """Runs the real sfizz_proc._popen with Popen mocked; returns argv."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sfizz_proc_copy", sfizz_proc.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module.subprocess, "Popen") as popen:
        module._popen(name, sfz_path)
    return popen.call_args[0][0]


class PadBankRenderTests(unittest.TestCase):
    def test_bank_gates_each_variant_on_the_pads_cc_and_empty_variant_is_silence(self):
        variants = [
            {"filename": "kick.wav", "volume_db": -2, "pan": 0, "cutoff_hz": None},
            {"filename": None},
            {"filename": "snare.wav", "volume_db": 3, "pan": 10, "cutoff_hz": 2000},
        ]
        text = sfz.render_pad_bank(5, 40, variants, {})
        cc = sfz.scene_cc(5)
        self.assertEqual(cc, 106)
        self.assertIn(f"<control> set_cc{cc}=0", text)
        regions = [l for l in text.splitlines() if l.startswith("<region>")]
        self.assertEqual(len(regions), 2)
        self.assertIn(f"locc{cc}=0 hicc{cc}=0", regions[0])
        self.assertIn(f"locc{cc}=2 hicc{cc}=2", regions[1])
        self.assertIn("key=40", regions[1])
        self.assertIn("cutoff=2000", regions[1])

    def test_bank_variants_put_live_first_and_dedupe(self):
        live = {"sample_id": 1, "filename": "a.wav", "volume_db": 0, "pan": 0, "cutoff_hz": None}
        scenes = [
            {"sample_id": 1, "filename": "a.wav", "volume_db": 0.0, "pan": 0.0, "cutoff_hz": None},
            {"sample_id": 2, "filename": "b.wav", "volume_db": 0, "pan": 0, "cutoff_hz": None},
            {"sample_id": None, "filename": None, "volume_db": 6, "pan": 0, "cutoff_hz": None},
            {"sample_id": None, "filename": None, "volume_db": -3, "pan": 0, "cutoff_hz": None},
        ]
        variants = orchestrator._bank_variants(live, scenes)
        self.assertEqual([v["filename"] for v in variants], ["a.wav", "b.wav", None])


class SceneFastPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p = patch.object(orchestrator, "_banks", {})
        p.start()
        self.addCleanup(p.stop)

    def _pad(self, n, sample_id, filename, volume=0.0):
        return {"pad_number": n, "midi_note": 35 + n, "sample_id": sample_id, "filename": filename,
                "volume_db": volume, "pan": 0.0, "cutoff_hz": None}

    async def test_sound_already_in_bank_switches_with_one_cc_and_no_new_process(self):
        a, b = self._pad(1, 1, "a.wav"), self._pad(1, 2, "b.wav")
        orchestrator._banks[1] = {
            "name": "diakopad_pad01_3", "note": 36, "sig": orchestrator._settings_sig({}),
            "keys": [orchestrator._variant_key(orchestrator._variant(a)),
                     orchestrator._variant_key(orchestrator._variant(b))],
            "slot": 0,
        }
        with (
            patch("engine.orchestrator.sfizz_proc.is_running", return_value=True),
            patch("engine.orchestrator.sfizz_proc.jack_name", return_value="diakopad_pad01_3"),
            patch("engine.orchestrator.midi.control_change", return_value=True) as cc,
            patch("engine.orchestrator.sfizz_proc.prepare") as prepare,
            patch("engine.orchestrator.apply_pad_effects", new=AsyncMock()),
            patch("engine.orchestrator.apply_fx_buses", new=AsyncMock()) as fx,
        ):
            await orchestrator.apply_changed_pads([a], [b], {}, [], fx=({}, []))
        cc.assert_called_once_with(midi.MIDI_CHANNEL, sfz.scene_cc(1), 1)
        prepare.assert_not_called()
        fx.assert_awaited_once()
        self.assertEqual(orchestrator._banks[1]["slot"], 1)

    async def test_sound_not_in_bank_goes_through_a_prepared_swap(self):
        a, c = self._pad(2, 1, "a.wav"), self._pad(2, 9, "c.wav")
        orchestrator._banks[2] = {
            "name": "diakopad_pad02_1", "note": 37, "sig": orchestrator._settings_sig({}),
            "keys": [orchestrator._variant_key(orchestrator._variant(a))], "slot": 0,
        }
        with (
            patch("engine.orchestrator.sfizz_proc.is_running", return_value=True),
            patch("engine.orchestrator.sfizz_proc.jack_name", return_value="diakopad_pad02_1"),
            patch("engine.orchestrator.midi.control_change", return_value=True) as cc,
            patch("engine.orchestrator.storage.scene_pad_variants", return_value={}),
            patch("engine.orchestrator.sfz.write_pad_bank", return_value=Path("p.sfz")),
            patch("engine.orchestrator.sfizz_proc.prepare", return_value="diakopad_pad02_2") as prepare,
            patch("engine.orchestrator.sfizz_proc.commit", return_value=True) as commit,
            patch("engine.orchestrator.jackgraph.wait_for_port_async", new=AsyncMock(return_value=True)),
            patch("engine.orchestrator.jackgraph.connect_pattern_to_all"),
            patch("engine.orchestrator.jackgraph.disconnect_all") as disconnect_all,
            patch("engine.orchestrator._apply_pad_effects_unlocked", new=AsyncMock()),
            patch("engine.orchestrator.apply_pad_effects", new=AsyncMock()),
        ):
            await orchestrator.apply_changed_pads([a], [c], {}, [])
        cc.assert_not_called()
        prepare.assert_called_once()
        commit.assert_called_once()
        # old generation stops receiving notes (but is not killed here)
        disconnect_all.assert_any_call(r"^diakopad_pad02_1:input$")

    async def test_note_or_settings_change_never_uses_a_stale_bank(self):
        a = self._pad(3, 1, "a.wav")
        orchestrator._banks[3] = {
            "name": "n", "note": 99, "sig": orchestrator._settings_sig({}),
            "keys": [orchestrator._variant_key(orchestrator._variant(a))], "slot": 0,
        }
        with (
            patch("engine.orchestrator.sfizz_proc.is_running", return_value=True),
            patch("engine.orchestrator.sfizz_proc.jack_name", return_value="n"),
        ):
            self.assertFalse(orchestrator._select_bank_slot(3, a, {}))
            orchestrator._banks[3]["note"] = a["midi_note"]
            self.assertTrue(orchestrator._select_bank_slot(3, a, {}))
            self.assertFalse(orchestrator._select_bank_slot(3, a, {"sustain_mode": "0"}))



class DiffRewireTests(unittest.TestCase):
    """Every JACK connect/disconnect is a graph reconfiguration (an xrun on
    a busy cycle): an unchanged chain must cost zero operations."""

    def _graph(self, edges):
        graph = {}
        for src, dst in edges:
            graph.setdefault(src, set()).add(dst)
        return lambda port: set(graph.get(port, set()))

    def test_unchanged_chain_touches_nothing_and_a_change_touches_only_the_diff(self):
        from engine import jackgraph
        with (
            patch.object(orchestrator, "_buses_ready", False),
            patch("engine.orchestrator.jackgraph.available", return_value=True),
            patch("engine.orchestrator.sfizz_proc.jack_name", return_value="diakopad_pad05_2"),
            patch("engine.orchestrator.sfizz_proc.retired_names", return_value=[]),
            patch("engine.orchestrator._master_destinations", return_value=("m:L", "m:R")),
        ):
            wired = {("diakopad_pad05_2:output_1", "m:L"), ("diakopad_pad05_2:output_2", "m:R")}
            with (
                patch.object(jackgraph, "connections", side_effect=self._graph(wired)),
                patch.object(jackgraph, "connect", return_value=True) as connect,
                patch.object(jackgraph, "disconnect", return_value=True) as disconnect,
            ):
                orchestrator._rewire_pad_chain(5, [])
            connect.assert_not_called()
            disconnect.assert_not_called()

            stale = wired | {("diakopad_pad05_2:output_1", "old:in")}
            with (
                patch.object(jackgraph, "connections", side_effect=self._graph(stale)),
                patch.object(jackgraph, "connect", return_value=True) as connect,
                patch.object(jackgraph, "disconnect", return_value=True) as disconnect,
            ):
                orchestrator._rewire_pad_chain(5, [])
            connect.assert_not_called()
            disconnect.assert_called_once_with("diakopad_pad05_2:output_1", "old:in")

class FxBusCatalogTests(unittest.TestCase):
    def test_delay_follows_tempo_in_beats(self):
        self.assertAlmostEqual(fx_buses.delay_time_ms(70, 0.75), 642.857, places=2)
        self.assertEqual(fx_buses.delay_time_ms(40, 0.75), fx_buses.DELAY_MAX_MS)
        values = fx_buses.plugin_values("delay", fx_buses.effective_params("delay", {"beats": 0.5}), 120)
        self.assertEqual(values["dt0"], 250.0)
        self.assertEqual(values["dm"], 1.0)  # dry muted on the bus
        self.assertNotIn("beats", values)

    def test_reverb_buses_are_wet_only(self):
        for bus in ("hall", "plate"):
            values = fx_buses.plugin_values(bus, fx_buses.effective_params(bus, {}), 100)
            self.assertEqual(values["dry_level"], 0.0)

    def test_send_gain_taper_and_matrix_slots(self):
        self.assertEqual(fx_buses.send_gain(0), 0.0)
        self.assertEqual(fx_buses.send_gain(100), 0.5)
        self.assertAlmostEqual(fx_buses.send_gain(50), 0.125)
        self.assertEqual(fx_buses.matrix_slot(1), (0, 1))
        self.assertEqual(fx_buses.matrix_slot(8), (0, 8))
        self.assertEqual(fx_buses.matrix_slot(9), (1, 1))
        self.assertEqual(fx_buses.matrix_slot(16), (1, 8))

    def test_effective_params_clamp_and_drop_unknown(self):
        params = fx_buses.effective_params("hall", {"decay": 99, "bogus": 1})
        self.assertEqual(params["decay"], 10.0)
        self.assertNotIn("bogus", params)


class ApplyFxBusesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in (("_buses_ready", False), ("_live_sends", {})):
            p = patch.object(orchestrator, name, value)
            p.start()
            self.addCleanup(p.stop)

    async def test_buses_are_created_once_and_scene_switches_only_set_numbers(self):
        add = AsyncMock(return_value=True)
        param_set = AsyncMock(return_value=True)
        with (
            patch("engine.orchestrator.modhost_client.is_configured", return_value=True),
            patch("engine.orchestrator.modhost_client.is_alive", return_value=True),
            patch("engine.orchestrator.modhost_client.add", add),
            patch("engine.orchestrator.modhost_client.remove", AsyncMock()) as remove,
            patch("engine.orchestrator.modhost_client.param_set", param_set),
            patch("engine.orchestrator.jackgraph.wait_for_port_async", new=AsyncMock(return_value=True)),
            patch("engine.orchestrator._wire_buses"),
            patch("engine.orchestrator.rewire_audio_routes", new=AsyncMock()) as rewire,
            patch("engine.orchestrator.storage.list_pad_effects", return_value=[]),
        ):
            sends = [{"pad_number": 1, "bus": "hall", "level": 50.0}, {"pad_number": 9, "bus": "delay", "level": 0.0}]
            self.assertTrue(await orchestrator.apply_fx_buses({}, sends))
            self.assertEqual(add.await_count, 5)  # 2 matrices + 3 buses
            rewire.assert_awaited_once()
            zeroed = [c for c in param_set.await_args_list if c.args[0] in orchestrator.MATRIX_INSTANCES and c.args[2] == 0.0]
            self.assertGreaterEqual(len(zeroed), 128)
            param_set.assert_any_await(8, "mix_1_1", fx_buses.send_gain(50.0))
            param_set.assert_any_await(9, "mix_1_3", 0.0)

            param_set.reset_mock()
            sends[0]["level"] = 70.0
            self.assertTrue(await orchestrator.apply_fx_buses({"hall": {"decay": 6.0}}, sends))
            self.assertEqual(add.await_count, 5, "a scene switch must never add plugins")
            remove.assert_not_awaited()
            sent = [c.args for c in param_set.await_args_list if c.args[0] in orchestrator.MATRIX_INSTANCES]
            self.assertEqual(sent, [(8, "mix_1_1", fx_buses.send_gain(70.0))], "only the send that changed")
            param_set.assert_any_await(orchestrator.BUS_INSTANCES["hall"], "decay", 6.0)

    async def test_partial_creation_is_rolled_back(self):
        add = AsyncMock(side_effect=[True, True, False])
        remove = AsyncMock(return_value=True)
        with (
            patch("engine.orchestrator.modhost_client.is_configured", return_value=True),
            patch("engine.orchestrator.modhost_client.is_alive", return_value=True),
            patch("engine.orchestrator.modhost_client.add", add),
            patch("engine.orchestrator.modhost_client.remove", remove),
        ):
            self.assertFalse(await orchestrator.apply_fx_buses({}, []))
        self.assertEqual([c.args[0] for c in remove.await_args_list], list(orchestrator.MATRIX_INSTANCES))
        self.assertFalse(orchestrator._buses_ready)


class MidiSceneCcFilterTests(unittest.TestCase):
    def test_pad_selector_ccs_are_never_forwarded_from_the_controller(self):
        self.assertTrue(midi.is_scene_cc(SimpleNamespace(type="control_change", control=102)))
        self.assertTrue(midi.is_scene_cc(SimpleNamespace(type="control_change", control=117)))
        self.assertFalse(midi.is_scene_cc(SimpleNamespace(type="control_change", control=101)))
        self.assertFalse(midi.is_scene_cc(SimpleNamespace(type="control_change", control=118)))
        self.assertFalse(midi.is_scene_cc(SimpleNamespace(type="note_on", control=None)))


class SendsAndBusesStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        p = patch.object(storage, "DB_PATH", Path(self._tmp.name) / "t.db")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        storage.init_db()

    def _save(self, name, **kwargs):
        return storage.save_scene(name, storage.list_pads(), storage.list_pad_effects(), [], **kwargs)

    def test_sends_and_bus_params_round_trip_through_a_scene(self):
        self.assertEqual(len(storage.list_pad_sends()), 48)
        storage.set_pad_send(2, "hall", 40)
        storage.set_fx_bus_param("delay", "beats", 0.5)
        sid = self._save("Ambiente", pad_sends=storage.list_pad_sends(), fx_buses=storage.list_fx_buses())

        storage.set_pad_send(2, "hall", 0)
        storage.set_fx_bus_param("delay", "beats", 1.0)
        storage.load_scene(sid)

        sends = {(s["pad_number"], s["bus"]): s["level"] for s in storage.list_pad_sends()}
        self.assertEqual(sends[(2, "hall")], 40)
        self.assertEqual(storage.list_fx_buses()["delay"]["beats"], 0.5)

    def test_scene_saved_before_buses_loads_with_all_sends_off_and_keeps_bus_params(self):
        sid = self._save("Antiga")  # no pad_sends / fx_buses, like pre-bus scenes
        storage.set_pad_send(5, "plate", 80)
        storage.set_fx_bus_param("hall", "decay", 7.0)
        storage.load_scene(sid)
        self.assertTrue(all(s["level"] == 0 for s in storage.list_pad_sends()))
        self.assertEqual(storage.list_fx_buses()["hall"]["decay"], 7.0)

    def test_scene_pad_variants_lists_each_scene_sound_per_pad(self):
        kick = storage.add_sample("kick.wav", "Kick")
        storage.assign_sample(1, kick)
        self._save("A")
        storage.assign_sample(1, None)
        self._save("B")
        variants = storage.scene_pad_variants()[1]
        self.assertEqual([v["filename"] for v in variants], ["kick.wav", None])


class FxBusEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        for p in (
            patch.object(storage, "DB_PATH", Path(self._tmp.name) / "t.db"),
            patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}),
            patch.object(orchestrator, "set_pad_send", AsyncMock(return_value=True)),
            patch.object(orchestrator, "set_fx_bus_param", AsyncMock(return_value=True)),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        storage.init_db()
        self.client = TestClient(diakopad_app.app)

    def test_get_lists_catalog_params_and_sends(self):
        data = self.client.get("/api/fx-buses").json()
        self.assertEqual([c["bus"] for c in data["catalog"]], ["hall", "plate", "delay"])
        self.assertEqual(data["params"]["delay"]["beats"], 0.75)
        self.assertEqual(len(data["sends"]), 48)

    def test_send_validation_and_persistence(self):
        self.assertEqual(self.client.post("/api/pads/3/sends/hall", json={"level": 101}).status_code, 400)
        self.assertEqual(self.client.post("/api/pads/17/sends/hall", json={"level": 10}).status_code, 400)
        self.assertEqual(self.client.post("/api/pads/3/sends/chorus", json={"level": 10}).status_code, 404)
        self.assertEqual(self.client.post("/api/pads/3/sends/hall", json={"level": 35}).status_code, 200)
        orchestrator.set_pad_send.assert_awaited_with(3, "hall", 35)
        sends = {(s["pad_number"], s["bus"]): s["level"] for s in storage.list_pad_sends()}
        self.assertEqual(sends[(3, "hall")], 35)

    def test_bus_param_validation_including_delay_divisions(self):
        self.assertEqual(self.client.post("/api/fx-buses/hall/param", json={"symbol": "decay", "value": 50}).status_code, 400)
        self.assertEqual(self.client.post("/api/fx-buses/hall/param", json={"symbol": "nope", "value": 1}).status_code, 400)
        self.assertEqual(self.client.post("/api/fx-buses/delay/param", json={"symbol": "beats", "value": 0.6}).status_code, 400)
        self.assertEqual(self.client.post("/api/fx-buses/delay/param", json={"symbol": "beats", "value": 0.5}).status_code, 200)
        self.assertEqual(storage.list_fx_buses()["delay"]["beats"], 0.5)


if __name__ == "__main__":
    unittest.main()

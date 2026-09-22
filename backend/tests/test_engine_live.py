import asyncio
import unittest
import time
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app as diakopad_app
import storage
from fastapi.testclient import TestClient
from app import trigger_pad as trigger_pad_endpoint
from engine import effects_catalog, jackgraph, looper, orchestrator, sfizz_proc


class _PortClient:
    def __init__(self):
        self.calls = 0

    def get_ports(self, _pattern):
        self.calls += 1
        if self.calls < 2:
            return []
        return ["diakopad_pad01:output_1"]


class JackGraphAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_port_async_yields_until_the_port_is_available(self):
        client = _PortClient()

        with patch("engine.jackgraph._get_client", return_value=client):
            found = await jackgraph.wait_for_port_async("diakopad_pad01:output_1", timeout=0.2)

        self.assertTrue(found)
        self.assertEqual(client.calls, 2)


class FullRestartTests(unittest.TestCase):
    def test_full_restart_starts_the_systemd_helper_unit(self):
        with patch("subprocess.Popen") as popen:
            response = TestClient(diakopad_app.app).post("/api/system/restart")

        self.assertEqual(response.status_code, 202)
        popen.assert_called_once_with(["systemctl", "--user", "start", "diakopad-restart.service"])


class LooperDuplicateHitWindowSettingsTests(unittest.TestCase):
    def test_duplicate_hit_window_setting_persists_and_applies_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_db_path = storage.DB_PATH
            original_window = looper._duplicate_hit_window_seconds
            storage.DB_PATH = Path(temp_dir) / "diakopad.db"
            storage.init_db()
            try:
                response = TestClient(diakopad_app.app).post(
                    "/api/settings/looper_duplicate_hit_window", json={"milliseconds": 80}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(storage.get_settings()["looper_duplicate_hit_window_ms"], "80")
                self.assertEqual(looper._duplicate_hit_window_seconds, 0.08)
            finally:
                storage.DB_PATH = original_db_path
                looper._duplicate_hit_window_seconds = original_window


class PadApplyResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_pad_keeps_the_event_loop_responsive_during_spawn(self):
        pads = [{"pad_number": 1, "filename": "kick.wav"}]

        def slow_spawn(_client, _sfz_path):
            time.sleep(0.2)
            return True

        with (
            patch("engine.orchestrator.sfz.write_pad_kit", return_value="pad01.sfz"),
            patch("engine.orchestrator.sfizz_proc.spawn", side_effect=slow_spawn),
            patch("engine.orchestrator.jackgraph.wait_for_port_async", new=AsyncMock(return_value=True)),
            patch("engine.orchestrator.jackgraph.connect_pattern_to_all", return_value=True),
            patch("engine.orchestrator.apply_pad_effects", new=AsyncMock()),
        ):
            task = asyncio.create_task(orchestrator.apply_pad(1, pads, {}, []))
            started = asyncio.get_running_loop().time()
            await asyncio.sleep(0.02)
            elapsed = asyncio.get_running_loop().time() - started
            await task

        self.assertLess(elapsed, 0.1)


class ApplyAllPadsClearsEmptyPadsTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_all_pads_stops_a_pad_that_lost_its_sample(self):
        """Regression: apply_all_pads used to only call apply_pad() for pads
        that currently have a filename, so a pad cleared by a scene switch
        (had a sample in the old scene, none in the new one) was never told
        to stop - its old sfizz instance kept running, still wired to
        hardware MIDI, and kept playing the previous scene's sample forever."""
        pads = [
            {"pad_number": 1, "filename": "kick.wav"},
            {"pad_number": 16, "filename": None},
        ]
        with (
            patch("engine.orchestrator.sfz.write_pad_kit", return_value="pad01.sfz"),
            patch("engine.orchestrator.sfizz_proc.spawn", return_value=True),
            patch("engine.orchestrator.sfizz_proc.stop") as stop,
            patch("engine.orchestrator.jackgraph.wait_for_port_async", new=AsyncMock(return_value=True)),
            patch("engine.orchestrator.jackgraph.connect_pattern_to_all", return_value=True),
        ):
            await orchestrator.apply_all_pads(pads, {}, [])

        stop.assert_called_once_with("diakopad_pad16")


class SfizzRecoveryQueueTests(unittest.TestCase):
    def test_schedule_recovery_does_not_override_an_existing_backoff(self):
        original_queue = sfizz_proc._next_recovery_at.copy()
        try:
            sfizz_proc._next_recovery_at["diakopad_pad01"] = 999.0
            sfizz_proc.schedule_recovery("diakopad_pad01")
            self.assertEqual(sfizz_proc._next_recovery_at["diakopad_pad01"], 999.0)
        finally:
            sfizz_proc._next_recovery_at.clear()
            sfizz_proc._next_recovery_at.update(original_queue)


class PadTriggerEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_trigger_while_recording_feeds_the_looper(self):
        pads = [{"pad_number": 1, "midi_note": 36}]
        with (
            patch("app.storage.list_pads", return_value=pads),
            patch("app.storage.get_settings", return_value={}),
            patch("app.trigger.trigger_pad", new=AsyncMock(return_value=True)),
            patch("app.looper.is_capturing", return_value=True),
            patch("app.looper.record_event") as record_event,
        ):
            result = await trigger_pad_endpoint(3)

        self.assertTrue(result["ok"])
        record_event.assert_called_once_with(3, 100)

    async def test_screen_trigger_sends_the_selected_pad_and_broadcasts_its_hit(self):
        pads = [{"pad_number": 1, "midi_note": 36}]
        with (
            patch("app.storage.list_pads", return_value=pads),
            patch("app.storage.get_settings", return_value={}),
            patch("app.trigger.trigger_pad", new=AsyncMock(return_value=True)) as trigger_pad,
            patch("app._queue_pad_hit") as queue_hit,
        ):
            result = await trigger_pad_endpoint(1)

        self.assertTrue(result["ok"])
        trigger_pad.assert_awaited_once_with(1, pads, 100, {})
        queue_hit.assert_called_once_with(1, 100)


class PadHitFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_physical_midi_note_is_broadcast_without_matching_pad(self):
        original_notes = diakopad_app._pad_notes
        diakopad_app._pad_notes = {}
        try:
            with patch("app.manager.broadcast", new=AsyncMock()) as broadcast:
                diakopad_app._handle_note(60, 90)
                await asyncio.sleep(0)

            broadcast.assert_awaited_once_with({"type": "midi_note", "note": 60, "velocity": 90})
        finally:
            diakopad_app._pad_notes = original_notes

    async def test_hit_queue_coalesces_repeated_hits_for_the_same_pad(self):
        with patch("app.manager.broadcast", new=AsyncMock()) as broadcast:
            diakopad_app._queue_pad_hit(1, 40)
            diakopad_app._queue_pad_hit(1, 100)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        broadcast.assert_awaited_once_with({"type": "pad_hit", "pad_number": 1, "velocity": 100})

    async def test_duplicate_midi_note_reports_every_matching_pad(self):
        original_notes = diakopad_app._pad_notes
        diakopad_app._pad_notes = {36: [1, 2]}
        try:
            with (
                patch("app._queue_pad_hit") as queue_hit,
                patch("app.looper.is_capturing", return_value=True),
                patch("app.looper.record_event") as record_event,
            ):
                diakopad_app._handle_note(36, 90)
        finally:
            diakopad_app._pad_notes = original_notes

        self.assertEqual(queue_hit.call_count, 2)
        self.assertEqual(record_event.call_count, 2)


class PadNoteLearnTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_note_learn_sets_the_pad_note_and_rebuilds_the_note_map(self):
        # _pending_note_learn is cleared by _handle_note before scheduling
        # this coroutine (see the other test below) - not this function's job.
        original_notes = diakopad_app._pad_notes
        try:
            with (
                patch("app.storage.set_pad_note") as set_pad_note,
                patch(
                    "app.storage.list_pads",
                    return_value=[{"pad_number": 3, "midi_note": 40, "sample_id": None}],
                ),
                patch("app.storage.list_pad_effects", return_value=[]),
                patch("app.storage.get_settings", return_value={}),
                patch("app.orchestrator.apply_pad", new=AsyncMock()) as apply_pad,
                patch("app.manager.broadcast", new=AsyncMock()) as broadcast,
            ):
                await diakopad_app._apply_note_learn(3, 40)

            set_pad_note.assert_called_once_with(3, 40)
            apply_pad.assert_awaited_once()
            self.assertEqual(diakopad_app._pad_notes, {40: [3]})
            broadcast.assert_any_await({"type": "note_learn", "pending_pad": None})
        finally:
            diakopad_app._pad_notes = original_notes
            diakopad_app._pending_note_learn = None

    async def test_hit_during_learn_is_captured_instead_of_treated_as_a_live_hit(self):
        diakopad_app._pending_note_learn = 5
        try:
            with (
                patch("app.asyncio.create_task") as create_task,
                patch("app._queue_pad_hit") as queue_hit,
            ):
                diakopad_app._handle_note(40, 90)

            self.assertEqual(create_task.call_count, 2)
            for call in create_task.call_args_list:
                call.args[0].close()  # scheduling was mocked out; avoid an "never awaited" warning
            queue_hit.assert_not_called()
            self.assertIsNone(diakopad_app._pending_note_learn)
        finally:
            diakopad_app._pending_note_learn = None


class EngineStatusTests(unittest.TestCase):
    def test_engine_status_reports_jack_modhost_and_pad_health(self):
        with (
            patch("engine.orchestrator.jackgraph.available", return_value=True),
            patch("engine.orchestrator.modhost_client.is_alive", return_value=True),
            patch("engine.orchestrator.sfizz_proc.is_running", side_effect=lambda client: client.endswith("01")),
            patch("engine.orchestrator.system_metrics.cpu_percent", return_value=42.5),
        ):
            status = orchestrator.engine_status()

        self.assertTrue(status["jack"])
        self.assertTrue(status["modhost"])
        self.assertTrue(status["pads"]["1"])
        self.assertFalse(status["pads"]["2"])
        self.assertEqual(status["cpu_percent"], 42.5)


class MasterGainTests(unittest.IsolatedAsyncioTestCase):
    def test_master_limiter_config_uses_the_validated_lsp_stereo_plugin(self):
        config = effects_catalog.master_limiter_config()

        self.assertEqual(config["lv2_uri"], "http://lsp-plug.in/plugins/lv2/limiter_stereo")
        self.assertEqual(config["in_ports"], ("in_l", "in_r"))
        self.assertEqual(config["out_ports"], ("out_l", "out_r"))
        self.assertEqual(config["symbol"], "th")


    async def test_master_inserts_limiter_before_the_gain_stage(self):
        original_available = orchestrator._master_available
        original_limiter_available = orchestrator._master_limiter_available
        try:
            orchestrator._master_available = False
            orchestrator._master_limiter_available = False
            with (
                patch("engine.orchestrator.effects_catalog.master_gain_config", return_value={
                    "lv2_uri": "gain", "in_ports": ("gain_l", "gain_r"), "out_ports": ("out_l", "out_r"),
                    "symbol": "trim", "min": -20, "max": 0,
                }),
                patch("engine.orchestrator.effects_catalog.master_limiter_config", return_value={
                    "lv2_uri": "limiter", "in_ports": ("in_l", "in_r"), "out_ports": ("out_l", "out_r"),
                    "symbol": "th", "enabled_symbol": "enabled",
                }),
                patch("engine.orchestrator.modhost_client.add", new=AsyncMock(return_value=True)) as add,
                patch("engine.orchestrator.modhost_client.param_set", new=AsyncMock(return_value=True)) as param_set,
                patch("engine.orchestrator._wire_master_output"),
                patch("engine.orchestrator.rewire_audio_routes", new=AsyncMock()) as rewire,
            ):
                applied = await orchestrator.apply_master(100, False, True, -1)

            self.assertTrue(applied)
            self.assertEqual(add.await_args_list[0].args, ("gain", orchestrator.MASTER_INSTANCE))
            self.assertEqual(add.await_args_list[1].args, ("limiter", orchestrator.MASTER_LIMITER_INSTANCE))
            param_set.assert_any_await(orchestrator.MASTER_LIMITER_INSTANCE, "enabled", 1.0)
            rewire.assert_awaited_once()
        finally:
            orchestrator._master_available = original_available
            orchestrator._master_limiter_available = original_limiter_available

    async def test_master_gain_falls_back_when_no_lv2_uri_is_configured(self):
        with patch(
            "engine.orchestrator.effects_catalog.master_gain_config",
            return_value={"lv2_uri": None},
        ):
            applied = await orchestrator.apply_master(100, False)

        self.assertFalse(applied)
        self.assertFalse(orchestrator.master_state()["available"])


class PitchEffectTests(unittest.TestCase):
    def test_pitch_effect_uses_the_validated_mapitchshift_ports_and_controls(self):
        plugin = effects_catalog.PLUGIN_CATALOG["pitch"]

        self.assertEqual(plugin["lv2_uri"], "http://distrho.sf.net/plugins/MaPitchshift")
        self.assertEqual(plugin["in_ports"], ("lv2_audio_in_1",))
        self.assertEqual(plugin["out_ports"], ("lv2_audio_out_1", "lv2_audio_out_2"))
        self.assertEqual([param["symbol"] for param in plugin["params"]], ["blur", "window", "ratio", "xfade"])

    def test_mono_input_effect_uses_left_channel_without_accessing_a_missing_right_port(self):
        key = (1, 1)
        previous = orchestrator._live_slot_plugin.get(key)
        orchestrator._live_slot_plugin[key] = "pitch"
        try:
            with (
                patch("engine.orchestrator.jackgraph.available", return_value=True),
                patch("engine.orchestrator.jackgraph.disconnect_all"),
                patch("engine.orchestrator.jackgraph.connect") as connect,
            ):
                orchestrator._rewire_pad_chain(1, [{"slot_index": 1}])

            connect.assert_any_call("diakopad_pad01:output_1", "effect_1011:lv2_audio_in_1")
            self.assertNotIn(
                (("diakopad_pad01:output_2", "effect_1011:lv2_audio_in_1"),), connect.call_args_list
            )
        finally:
            if previous is None:
                del orchestrator._live_slot_plugin[key]
            else:
                orchestrator._live_slot_plugin[key] = previous


class PanicTests(unittest.IsolatedAsyncioTestCase):
    async def test_panic_stops_transports_sends_all_notes_off_and_mutes_master(self):
        with (
            patch("engine.orchestrator.sequencer.stop", new=AsyncMock()) as sequencer_stop,
            patch("engine.orchestrator.metronome.stop", new=AsyncMock()) as metronome_stop,
            patch("engine.orchestrator.looper.stop") as looper_stop,
            patch("engine.orchestrator.midi.all_notes_off", return_value=True) as all_notes_off,
            patch("engine.orchestrator.midi.note_off", return_value=True) as note_off,
            patch("engine.orchestrator.apply_master", new=AsyncMock(return_value=True)) as apply_master,
            patch("engine.orchestrator.sfizz_proc.emergency_stop_all") as emergency_stop_all,
        ):
            applied = await orchestrator.panic([{"midi_note": 36}, {"midi_note": 38}])

        self.assertTrue(applied)
        sequencer_stop.assert_awaited_once()
        metronome_stop.assert_awaited_once()
        looper_stop.assert_called_once()
        all_notes_off.assert_called_once_with(orchestrator.midi.MIDI_CHANNEL)
        self.assertEqual(note_off.call_count, 2)
        apply_master.assert_awaited_once_with(
            orchestrator.master_state()["volume"],
            True,
            orchestrator.master_state()["limiter_enabled"],
            orchestrator.master_state()["limiter_threshold_db"],
        )
        emergency_stop_all.assert_called_once()

    async def test_panic_terminates_players_when_master_mute_is_unavailable(self):
        with (
            patch("engine.orchestrator.sequencer.stop", new=AsyncMock()),
            patch("engine.orchestrator.metronome.stop", new=AsyncMock()),
            patch("engine.orchestrator.looper.stop"),
            patch("engine.orchestrator.midi.all_notes_off", return_value=True),
            patch("engine.orchestrator.midi.note_off", return_value=True),
            patch("engine.orchestrator.apply_master", new=AsyncMock(return_value=False)),
            patch("engine.orchestrator.sfizz_proc.emergency_stop_all") as emergency_stop_all,
        ):
            applied = await orchestrator.panic([{"midi_note": 36}])

        self.assertFalse(applied)
        emergency_stop_all.assert_called_once()


class SceneRoundtripTests(unittest.TestCase):
    def _with_temp_db(self):
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        return temp_ctx, original_db_path

    def test_save_and_load_scene_restores_pads_and_effects(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_effect_slot(1, 1, "reverb")
            pads_before = storage.list_pads()
            scene_id = storage.save_scene("show-a", pads_before, storage.list_pad_effects())

            storage.set_pad_effect_slot(1, 1, None)
            storage.set_pad_effect_slot(2, 2, "delay")
            storage.set_pad_mix(1, volume_db=-3, pan=0.5)
            loaded = storage.load_scene(scene_id)

            self.assertIsNotNone(loaded)
            restored = {
                (e["pad_number"], e["slot_index"]): e["plugin_id"]
                for e in storage.list_pad_effects()
            }
            self.assertEqual(restored[(1, 1)], "reverb")
            self.assertEqual(restored[(2, 2)], None)
            self.assertEqual(storage.list_pads(), pads_before)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_load_scene_preserves_midi_notes(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_note(3, 99)
            scene_id = storage.save_scene("notes", storage.list_pads(), storage.list_pad_effects())

            storage.load_scene(scene_id)

            self.assertEqual(
                next(p["midi_note"] for p in storage.list_pads() if p["pad_number"] == 3), 99
            )
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_save_and_load_scene_roundtrips_knob_mappings(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_effect_slot(1, 1, "reverb")
            storage.set_knob_mapping(74, "global", None, "tempo")
            storage.set_knob_mapping(71, "pad", 1, "slot1:decay")
            scene_id = storage.save_scene(
                "knobs", storage.list_pads(), storage.list_pad_effects(), storage.list_knob_mappings()
            )

            storage.delete_knob_mapping(74)
            storage.set_knob_mapping(75, "global", None, "tempo")
            loaded = storage.load_scene(scene_id)

            self.assertIsNotNone(loaded)
            mappings = {
                m["cc_number"]: (m["scope"], m["pad_number"], m["param"])
                for m in storage.list_knob_mappings()
            }
            self.assertEqual(mappings[74], ("global", None, "tempo"))
            self.assertEqual(mappings[71], ("pad", 1, "slot1:decay"))
            self.assertNotIn(75, mappings)  # live-only mapping was replaced
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_legacy_scene_without_knobs_leaves_current_mappings_untouched(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            # Saved before knobs joined the snapshot: no knob rows captured.
            scene_id = storage.save_scene("legacy", storage.list_pads(), storage.list_pad_effects())

            storage.set_knob_mapping(74, "global", None, "tempo")
            loaded = storage.load_scene(scene_id)

            self.assertIsNotNone(loaded)
            mappings = {m["cc_number"] for m in storage.list_knob_mappings()}
            self.assertIn(74, mappings)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_save_scene_overwrites_same_name(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_effect_slot(1, 1, "reverb")
            first = storage.save_scene("dup", storage.list_pads(), storage.list_pad_effects())
            storage.set_pad_effect_slot(1, 1, None)
            second = storage.save_scene("dup", storage.list_pads(), storage.list_pad_effects())

            self.assertEqual(first, second)
            scene = storage.get_scene(first)
            plugin = next(
                e["plugin_id"] for e in scene["effects"] if e["pad_number"] == 1 and e["slot_index"] == 1
            )
            self.assertIsNone(plugin)
            self.assertEqual(len(storage.list_scenes()), 1)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


class SceneLoadEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_scene_applies_engine_and_rebuilds_note_map(self):
        original_notes = diakopad_app._pad_notes
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        try:
            storage.set_pad_note(3, 99)
            scene_id = storage.save_scene("live", storage.list_pads(), storage.list_pad_effects())
            with (
                patch("app.orchestrator.apply_all_pads", new=AsyncMock()) as apply_all,
                patch("app._broadcast_pads", new=AsyncMock()) as broadcast_pads,
                patch("app._broadcast_pad_effects", new=AsyncMock()),
            ):
                result = await diakopad_app.load_scene(scene_id)

            self.assertTrue(result["ok"])
            apply_all.assert_awaited_once()
            broadcast_pads.assert_awaited_once()
            expected_notes = {35 + n: [n] for n in range(1, 17)}
            del expected_notes[38]
            expected_notes[99] = [3]
            self.assertEqual(diakopad_app._pad_notes, expected_notes)
        finally:
            diakopad_app._pad_notes = original_notes
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_load_scene_missing_returns_404(self):
        from fastapi import HTTPException

        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        try:
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.load_scene(999)
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


class ControllerBindingStorageTests(unittest.TestCase):
    def _with_temp_db(self):
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        return temp_ctx, original_db_path

    def test_relearning_a_signal_replaces_its_old_binding_only(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("scene_next", "note", 45)
            storage.add_controller_binding("scene_next", "cc", 20)

            storage.add_controller_binding("looper_record_toggle", "note", 45)

            self.assertEqual(storage.get_action_for_signal("note", 45), "looper_record_toggle")
            self.assertEqual(storage.get_action_for_signal("cc", 20), "scene_next")
            bindings = storage.list_controller_bindings()
            self.assertEqual(len(bindings["scene_next"]), 1)
            self.assertEqual(len(bindings["looper_record_toggle"]), 1)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_one_action_can_have_several_bindings(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("scene_next", "note", 10)
            storage.add_controller_binding("scene_next", "note", 11)

            bindings = storage.list_controller_bindings()["scene_next"]
            self.assertEqual({b["number"] for b in bindings}, {10, 11})
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_delete_controller_binding(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            binding_id = storage.add_controller_binding("scene_next", "note", 10)

            self.assertTrue(storage.delete_controller_binding(binding_id))
            self.assertIsNone(storage.get_action_for_signal("note", 10))
            self.assertFalse(storage.delete_controller_binding(binding_id))
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


class ControllerActionDispatchTests(unittest.IsolatedAsyncioTestCase):
    def _with_temp_db(self):
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        return temp_ctx, original_db_path

    async def test_handle_note_dispatches_bound_action_instead_of_a_pad(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("scene_next", "note", 45)
            with (
                patch("app._dispatch_controller_action", new=AsyncMock()) as dispatch,
                patch("app._queue_pad_hit") as queue_hit,
                patch("app.manager.broadcast", new=AsyncMock()),
            ):
                diakopad_app._handle_note(45, 100)
                await asyncio.sleep(0)

            dispatch.assert_awaited_once_with("scene_next")
            queue_hit.assert_not_called()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_handle_cc_dispatches_bound_action_instead_of_a_knob(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("looper_record_toggle", "cc", 20)
            with (
                patch("app._dispatch_controller_action", new=AsyncMock()) as dispatch,
                patch("app.storage.get_knob_target") as knob_target,
            ):
                diakopad_app._handle_cc(20, 127)
                await asyncio.sleep(0)

            dispatch.assert_awaited_once_with("looper_record_toggle")
            knob_target.assert_not_called()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_handle_cc_bound_action_fires_once_per_button_press(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("scene_next", "cc", 20)
            with patch("app._dispatch_controller_action", new=AsyncMock()) as dispatch:
                # A momentary button: a burst of nonzero values while held,
                # then 0 on release - must dispatch exactly once.
                diakopad_app._handle_cc(20, 127)
                diakopad_app._handle_cc(20, 127)
                diakopad_app._handle_cc(20, 126)
                diakopad_app._handle_cc(20, 0)
                await asyncio.sleep(0)

                dispatch.assert_awaited_once_with("scene_next")

                # Re-armed by the release: a new press dispatches again.
                diakopad_app._handle_cc(20, 127)
                await asyncio.sleep(0)

                self.assertEqual(dispatch.await_count, 2)
        finally:
            diakopad_app._cc_action_last_values.clear()
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_handle_note_bound_action_debounces_hardware_double_fire(self):
        # Some physical buttons send two genuine (velocity > 0) Note On
        # messages a few ms apart for a single press - not the already
        # handled velocity<=0 release encoding. Left undebounced, a
        # toggle-style action (kit_browse_toggle, panic, ...) would fire
        # twice and immediately cancel itself.
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("kit_browse_toggle", "note", 54)
            with patch("app._dispatch_controller_action", new=AsyncMock()) as dispatch:
                diakopad_app._handle_note(54, 127)
                diakopad_app._handle_note(54, 127)
                await asyncio.sleep(0)

                dispatch.assert_awaited_once_with("kit_browse_toggle")
        finally:
            diakopad_app._note_last_dispatch.clear()
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    @staticmethod
    def _browsing_state_with_three_sounds():
        # Pad 15 maps to "right" (next sound); three sounds so a single step
        # (1 -> 2) is distinguishable from an undebounced double step (1 -> 3)
        # without any wraparound muddying the assertion.
        return {
            "target_pad": 1,
            "kits": [
                {
                    "id": 1,
                    "name": "Kit",
                    "pads": [
                        {"pad_number": 1, "sample_id": 11},
                        {"pad_number": 2, "sample_id": 12},
                        {"pad_number": 3, "sample_id": 13},
                    ],
                }
            ],
            "kit_idx": 0,
            "candidate_pad_number": 1,
        }

    def _with_kit_browse_right_pad(self):
        original_notes = diakopad_app._pad_notes
        diakopad_app._pad_notes = {36: [15]}  # 15 = right (next sound)
        return original_notes

    async def test_kit_browse_note_debounces_hardware_double_fire(self):
        # The same SMC-PAD firmware double-fire as the bound-action debounce
        # above, on the kit-browse navigation path: undebounced, every
        # physical tap stepped kits/sounds twice ("jumping two by two").
        original_notes = self._with_kit_browse_right_pad()
        diakopad_app._kit_browse_state = self._browsing_state_with_three_sounds()
        try:
            with (
                patch("app._broadcast_kit_browse", new=AsyncMock()),
                patch("app._kit_browse_maybe_preview", new=AsyncMock()),
            ):
                diakopad_app._handle_note(36, 100)
                diakopad_app._handle_note(36, 100)  # firmware double-fire, ms later
                await asyncio.sleep(0)

            self.assertEqual(diakopad_app._kit_browse_state["candidate_pad_number"], 2)
        finally:
            diakopad_app._pad_notes = original_notes
            diakopad_app._kit_browse_state = None
            diakopad_app._note_last_dispatch.clear()

    async def test_kit_browse_note_redispatches_after_the_debounce_window(self):
        original_notes = self._with_kit_browse_right_pad()
        diakopad_app._kit_browse_state = self._browsing_state_with_three_sounds()
        try:
            with (
                patch("app._broadcast_kit_browse", new=AsyncMock()),
                patch("app._kit_browse_maybe_preview", new=AsyncMock()),
            ):
                diakopad_app._handle_note(36, 100)
                await asyncio.sleep(0)
                time.sleep(diakopad_app.BOUND_NOTE_DEBOUNCE_SECONDS + 0.01)  # past the window
                diakopad_app._handle_note(36, 100)  # a deliberate second tap
                await asyncio.sleep(0)

            self.assertEqual(diakopad_app._kit_browse_state["candidate_pad_number"], 3)
        finally:
            diakopad_app._pad_notes = original_notes
            diakopad_app._kit_browse_state = None
            diakopad_app._note_last_dispatch.clear()

    async def test_handle_note_captures_a_pending_learn_instead_of_dispatching(self):
        temp_ctx, original_db_path = self._with_temp_db()
        diakopad_app._pending_controller_learn = "scene_next"
        try:
            with patch("app.manager.broadcast", new=AsyncMock()) as broadcast:
                diakopad_app._handle_note(77, 100)
                await asyncio.sleep(0)

            self.assertEqual(storage.get_action_for_signal("note", 77), "scene_next")
            self.assertIsNone(diakopad_app._pending_controller_learn)
            broadcast.assert_any_await(
                {
                    "type": "controller_actions",
                    "bindings": storage.list_controller_bindings(),
                    "pending_learn": None,
                }
            )
        finally:
            diakopad_app._pending_controller_learn = None
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_looper_record_toggle_starts_when_idle(self):
        with (
            patch("app.looper.get_state", return_value={"state": "stopped"}),
            patch("app.looper.record_start") as record_start,
            patch("app.looper.record_stop", new=AsyncMock()) as record_stop,
            patch("app._broadcast_looper", new=AsyncMock()) as broadcast_looper,
            patch("app.manager.broadcast", new=AsyncMock()) as broadcast,
        ):
            await diakopad_app._dispatch_controller_action("looper_record_toggle")

        record_start.assert_called_once()
        record_stop.assert_not_awaited()
        broadcast_looper.assert_awaited_once()
        broadcast.assert_awaited_once_with({"type": "navigate", "view": "looper"})

    async def test_looper_record_toggle_stops_when_recording(self):
        with (
            patch("app.looper.get_state", return_value={"state": "recording"}),
            patch("app.looper.record_start") as record_start,
            patch("app.looper.record_stop", new=AsyncMock()) as record_stop,
            patch("app._broadcast_looper", new=AsyncMock()),
            patch("app.manager.broadcast", new=AsyncMock()) as broadcast,
        ):
            await diakopad_app._dispatch_controller_action("looper_record_toggle")

        record_stop.assert_awaited_once()
        record_start.assert_not_called()
        broadcast.assert_awaited_once_with({"type": "navigate", "view": "looper"})

    async def test_looper_play_toggle_stops_a_running_loop(self):
        with (
            patch("app.looper.get_state", return_value={"state": "playing"}),
            patch("app.looper.stop") as stop,
            patch("app.looper.play_start", new=AsyncMock()) as play_start,
            patch("app._broadcast_looper", new=AsyncMock()),
            patch("app.manager.broadcast", new=AsyncMock()),
        ):
            await diakopad_app._dispatch_controller_action("looper_play_toggle")

        stop.assert_called_once()
        play_start.assert_not_awaited()

    async def test_looper_play_toggle_resumes_a_stopped_loop(self):
        with (
            patch("app.looper.get_state", return_value={"state": "stopped"}),
            patch("app.looper.stop") as stop,
            patch("app.looper.play_start", new=AsyncMock()) as play_start,
            patch("app.storage.list_pads", return_value=[]),
            patch("app.storage.get_settings", return_value={}),
            patch("app._broadcast_looper", new=AsyncMock()),
            patch("app.manager.broadcast", new=AsyncMock()),
        ):
            await diakopad_app._dispatch_controller_action("looper_play_toggle")

        play_start.assert_awaited_once_with([], {})
        stop.assert_not_called()

    async def test_looper_overdub_toggle_starts_overdub_when_playing(self):
        with (
            patch("app.looper.get_state", return_value={"state": "playing"}),
            patch("app.looper.overdub_start") as overdub_start,
            patch("app.looper.overdub_stop") as overdub_stop,
            patch("app._broadcast_looper", new=AsyncMock()),
            patch("app.manager.broadcast", new=AsyncMock()),
        ):
            await diakopad_app._dispatch_controller_action("looper_overdub_toggle")

        overdub_start.assert_called_once()
        overdub_stop.assert_not_called()

    async def test_looper_overdub_toggle_closes_overdub_when_overdubbing(self):
        with (
            patch("app.looper.get_state", return_value={"state": "overdubbing"}),
            patch("app.looper.overdub_start") as overdub_start,
            patch("app.looper.overdub_stop") as overdub_stop,
            patch("app._broadcast_looper", new=AsyncMock()),
            patch("app.manager.broadcast", new=AsyncMock()),
        ):
            await diakopad_app._dispatch_controller_action("looper_overdub_toggle")

        overdub_stop.assert_called_once()
        overdub_start.assert_not_called()

    async def test_scene_next_advances_and_wraps_around_to_the_first_scene(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            scene_a = storage.save_scene("a", storage.list_pads(), storage.list_pad_effects())
            scene_b = storage.save_scene("b", storage.list_pads(), storage.list_pad_effects())
            storage.set_setting("current_scene_id", str(scene_b))

            with patch("app._apply_scene_and_broadcast", new=AsyncMock()) as apply_scene:
                await diakopad_app._dispatch_controller_action("scene_next")

            apply_scene.assert_awaited_once()
            self.assertEqual(storage.get_settings()["current_scene_id"], str(scene_a))
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_scene_prev_moves_backwards_and_wraps_around_to_the_last_scene(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            scene_a = storage.save_scene("a", storage.list_pads(), storage.list_pad_effects())
            scene_b = storage.save_scene("b", storage.list_pads(), storage.list_pad_effects())
            storage.set_setting("current_scene_id", str(scene_a))

            with patch("app._apply_scene_and_broadcast", new=AsyncMock()) as apply_scene:
                await diakopad_app._dispatch_controller_action("scene_prev")

            apply_scene.assert_awaited_once()
            self.assertEqual(storage.get_settings()["current_scene_id"], str(scene_b))
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_scene_next_is_a_noop_when_there_are_no_scenes(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with patch("app._apply_scene_and_broadcast", new=AsyncMock()) as apply_scene:
                await diakopad_app._dispatch_controller_action("scene_next")

            apply_scene.assert_not_awaited()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_scene_next_skips_inactive_scenes(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            scene_a = storage.save_scene("a", storage.list_pads(), storage.list_pad_effects())
            scene_b = storage.save_scene("b", storage.list_pads(), storage.list_pad_effects())
            storage.set_scene_active(scene_a, False)
            storage.set_setting("current_scene_id", str(scene_b))

            with patch("app._apply_scene_and_broadcast", new=AsyncMock()) as apply_scene:
                await diakopad_app._dispatch_controller_action("scene_next")

            apply_scene.assert_awaited_once()
            # Only scene_b is active, so next wraps back onto itself.
            self.assertEqual(storage.get_settings()["current_scene_id"], str(scene_b))
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_panic_controller_action_runs_the_panic_flow(self):
        with (
            patch("app.orchestrator.panic", new=AsyncMock(return_value=True)) as panic,
            patch("app.storage.list_pads", return_value=[{"pad_number": 1}]),
            patch("app._broadcast_master", new=AsyncMock()) as broadcast_master,
            patch("app._broadcast_looper", new=AsyncMock()),
            patch("app._broadcast_sequencer", new=AsyncMock()),
            patch("app._broadcast_metronome", new=AsyncMock()),
        ):
            await diakopad_app._dispatch_controller_action("panic")

        panic.assert_awaited_once_with([{"pad_number": 1}])
        broadcast_master.assert_awaited_once()

    async def test_tap_tempo_controller_action_sets_the_tapped_bpm(self):
        with (
            patch("app.tempo.register_tap", return_value=128.0) as register_tap,
            patch("app.tempo.set") as tempo_set,
            patch("app._broadcast_tempo", new=AsyncMock()) as broadcast_tempo,
        ):
            await diakopad_app._dispatch_controller_action("tap_tempo")

        register_tap.assert_called_once()
        tempo_set.assert_called_once_with(128.0)
        broadcast_tempo.assert_awaited_once()

    async def test_tap_tempo_controller_action_waits_for_the_second_tap(self):
        with (
            patch("app.tempo.register_tap", return_value=None),
            patch("app.tempo.set") as tempo_set,
            patch("app._broadcast_tempo", new=AsyncMock()) as broadcast_tempo,
        ):
            await diakopad_app._dispatch_controller_action("tap_tempo")

        tempo_set.assert_not_called()
        broadcast_tempo.assert_not_awaited()

    async def test_note_bound_tap_registers_once_per_note_on(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("tap_tempo", "note", 54)
            with (
                patch("app.tempo.register_tap", return_value=None) as register_tap,
                patch("app._broadcast_midi_note", new=AsyncMock()),
            ):
                diakopad_app._handle_note(54, 127)
                await asyncio.sleep(0)

            register_tap.assert_called_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_zero_velocity_note_never_dispatches_a_controller_action(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.add_controller_binding("tap_tempo", "note", 54)
            with (
                patch("app.tempo.register_tap", return_value=None) as register_tap,
                patch("app._broadcast_midi_note", new=AsyncMock()),
            ):
                # A release encoded as "Note On velocity 0" must never count as
                # a tap (only a genuine Note On does).
                diakopad_app._handle_note(54, 0)
                await asyncio.sleep(0)

            register_tap.assert_not_called()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_apply_scene_restores_global_state_and_transports(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_setting("sequencer_bpm", "150")
            storage.set_setting("metronome_signature", "5_4")
            storage.set_sequencer_step(2, 3, True)
            scene_id = storage.save_scene(
                "cena",
                storage.list_pads(),
                storage.list_pad_effects(),
                storage.list_knob_mappings(),
                scene_state={
                    "sequencer_bpm": "150",
                    "metronome_style": "digital",
                    "metronome_signature": "5_4",
                    "metronome_running": "1",
                    "sequencer_running": "1",
                },
                sequencer_steps=storage.list_sequencer_steps(),
            )
            scene = storage.load_scene(scene_id)

            with (
                patch("app.tempo.set") as tempo_set,
                patch("app.metronome.set_signature") as set_signature,
                patch("app.orchestrator.apply_metronome_style", new=AsyncMock()),
                patch("app.orchestrator.apply_all_pads", new=AsyncMock()),
                patch("app.sequencer.load_pattern") as load_pattern,
                patch("app.sequencer.start", new=AsyncMock()) as seq_start,
                patch("app.metronome.start", new=AsyncMock()) as met_start,
                patch("app._broadcast_pads", new=AsyncMock()),
                patch("app._broadcast_pad_effects", new=AsyncMock()),
                patch("app._broadcast_knobs", new=AsyncMock()),
                patch("app._broadcast_tempo", new=AsyncMock()),
                patch("app._broadcast_sequencer", new=AsyncMock()),
                patch("app._broadcast_metronome", new=AsyncMock()),
            ):
                await diakopad_app._apply_scene_and_broadcast(scene)

            tempo_set.assert_called_once_with(150.0)
            set_signature.assert_called_once_with("5_4")
            load_pattern.assert_called_once()
            seq_start.assert_awaited_once()
            met_start.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


class LooperKnobTargetTests(unittest.IsolatedAsyncioTestCase):
    """Turning a knob bound to the looper_track/looper_mute global params
    drives the armed-track selection and its mute (app.py's
    _apply_knob_target), with the navigate-to-looper broadcast so a
    hardware selection is mirrored on screen."""

    def setUp(self):
        looper._tracks = [looper.Track() for _ in range(looper.TRACK_COUNT)]
        looper._selected = 0

    def tearDown(self):
        looper._tracks = [looper.Track() for _ in range(looper.TRACK_COUNT)]
        looper._selected = 0
        diakopad_app._pending_cc_values.clear()

    async def test_knob_turn_selects_the_looper_track(self):
        diakopad_app._pending_cc_values[("global", None, "looper_track")] = 127

        with (
            patch("app.storage.list_pad_effects", return_value=[]),
            patch("app.manager.broadcast", new=AsyncMock()) as broadcast,
        ):
            await diakopad_app._apply_knob_target(("global", None, "looper_track"))

        self.assertEqual(looper.get_selected(), 3)
        broadcast.assert_any_await({"type": "navigate", "view": "looper"})

    async def test_knob_turn_mutes_the_armed_track(self):
        diakopad_app._pending_cc_values[("global", None, "looper_mute")] = 127
        looper._selected = 2

        with (
            patch("app.storage.list_pad_effects", return_value=[]),
            patch("app.manager.broadcast", new=AsyncMock()),
        ):
            await diakopad_app._apply_knob_target(("global", None, "looper_mute"))

        self.assertTrue(looper._tracks[2].muted)


class EffectSlotApplyTests(unittest.IsolatedAsyncioTestCase):
    """Scenes saved before a catalog swap (e.g. mda/Ambience -> Dragonfly
    reverb) carry param symbols the new plugin doesn't have - they must be
    filtered out (with defaults filling in) before reaching param_set."""

    def setUp(self):
        self._original_live = dict(orchestrator._live_slot_plugin)
        orchestrator._live_slot_plugin.clear()

    def tearDown(self):
        orchestrator._live_slot_plugin.clear()
        orchestrator._live_slot_plugin.update(self._original_live)

    async def test_stale_params_are_filtered_before_param_set(self):
        pad_effects = [
            {
                "pad_number": 1,
                "slot_index": 1,
                "plugin_id": "reverb",
                "params": {"mix": 0.3, "size": 0.5, "hf_damp": 0.5, "decay": 4.0},
            }
        ]
        with (
            patch("engine.orchestrator.modhost_client.is_alive", return_value=True),
            patch("engine.orchestrator.modhost_client.add", new=AsyncMock(return_value=True)) as add,
            patch(
                "engine.orchestrator.modhost_client.param_set", new=AsyncMock(return_value=True)
            ) as param_set,
            patch("engine.orchestrator.jackgraph.available", return_value=False),
        ):
            await orchestrator._apply_pad_effects_unlocked(1, pad_effects)

        add.assert_awaited_once()
        sent = {call.args[1]: call.args[2] for call in param_set.await_args_list}
        self.assertNotIn("mix", sent)
        self.assertEqual(
            sent, effects_catalog.effective_params("reverb", pad_effects[0]["params"])
        )


if __name__ == "__main__":
    unittest.main()

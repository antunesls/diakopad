import asyncio
import unittest
import time
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app as diakopad_app
import storage
from app import trigger_pad as trigger_pad_endpoint
from engine import jackgraph, orchestrator, sfizz_proc


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
                patch("app.looper.get_state", return_value={"state": "recording"}),
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
        ):
            status = orchestrator.engine_status()

        self.assertTrue(status["jack"])
        self.assertTrue(status["modhost"])
        self.assertTrue(status["pads"]["1"])
        self.assertFalse(status["pads"]["2"])


class MasterGainTests(unittest.IsolatedAsyncioTestCase):
    async def test_master_gain_falls_back_when_no_lv2_uri_is_configured(self):
        with patch(
            "engine.orchestrator.effects_catalog.master_gain_config",
            return_value={"lv2_uri": None},
        ):
            applied = await orchestrator.apply_master(100, False)

        self.assertFalse(applied)
        self.assertFalse(orchestrator.master_state()["available"])


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
        apply_master.assert_awaited_once_with(orchestrator.master_state()["volume"], True)
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


class KitRoundtripTests(unittest.TestCase):
    def _with_temp_db(self):
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        return temp_ctx, original_db_path

    def test_save_and_load_kit_restores_pads_and_effects(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_effect_slot(1, 1, "reverb")
            pads_before = storage.list_pads()
            kit_id = storage.save_kit("show-a", pads_before, storage.list_pad_effects())

            storage.set_pad_effect_slot(1, 1, None)
            storage.set_pad_effect_slot(2, 2, "delay")
            storage.set_pad_mix(1, volume_db=-3, pan=0.5)
            loaded = storage.load_kit(kit_id)

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

    def test_load_kit_preserves_midi_notes(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_note(3, 99)
            kit_id = storage.save_kit("notes", storage.list_pads(), storage.list_pad_effects())

            storage.load_kit(kit_id)

            self.assertEqual(
                next(p["midi_note"] for p in storage.list_pads() if p["pad_number"] == 3), 99
            )
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    def test_save_kit_overwrites_same_name(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            storage.set_pad_effect_slot(1, 1, "reverb")
            first = storage.save_kit("dup", storage.list_pads(), storage.list_pad_effects())
            storage.set_pad_effect_slot(1, 1, None)
            second = storage.save_kit("dup", storage.list_pads(), storage.list_pad_effects())

            self.assertEqual(first, second)
            kit = storage.get_kit(first)
            plugin = next(
                e["plugin_id"] for e in kit["effects"] if e["pad_number"] == 1 and e["slot_index"] == 1
            )
            self.assertIsNone(plugin)
            self.assertEqual(len(storage.list_kits()), 1)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


class KitLoadEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_kit_applies_engine_and_rebuilds_note_map(self):
        original_notes = diakopad_app._pad_notes
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        try:
            storage.set_pad_note(3, 99)
            kit_id = storage.save_kit("live", storage.list_pads(), storage.list_pad_effects())
            with (
                patch("app.orchestrator.apply_all_pads", new=AsyncMock()) as apply_all,
                patch("app._broadcast_pads", new=AsyncMock()) as broadcast_pads,
                patch("app._broadcast_pad_effects", new=AsyncMock()),
            ):
                result = await diakopad_app.load_kit(kit_id)

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

    async def test_load_kit_missing_returns_404(self):
        from fastapi import HTTPException

        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        try:
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.load_kit(999)
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


if __name__ == "__main__":
    unittest.main()

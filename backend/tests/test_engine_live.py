import asyncio
import unittest
import time
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import storage
from engine import looper, metronome, metronome_sounds, sequencer, tempo, trigger


class SequencerRegressionTests(unittest.TestCase):
    def test_toggle_step_persists_the_new_state(self):
        original_db_path = storage.DB_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            storage.DB_PATH = Path(temp_dir) / "test.db"
            try:
                storage.init_db()
                sequencer.load_pattern(storage.list_sequencer_steps())
                sequencer.toggle_step(1, 0, True)

                active = next(
                    step["active"]
                    for step in storage.list_sequencer_steps()
                    if step["pad_number"] == 1 and step["step_index"] == 0
                )
                self.assertTrue(active)
            finally:
                storage.DB_PATH = original_db_path


class TriggerRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_pad_schedules_note_off_when_sustain_is_disabled(self):
        pads = [{"pad_number": 1, "midi_note": 36}]

        with (
            patch("engine.trigger.midi.note_on", return_value=True) as note_on,
            patch("engine.trigger.midi.note_off") as note_off,
            patch("engine.trigger.asyncio.get_event_loop") as get_event_loop,
        ):
            get_event_loop.return_value.call_later.side_effect = (
                lambda _delay, callback, *args: callback(*args)
            )

            sent = await trigger.trigger_pad(1, pads, velocity=90, settings={"sustain_mode": "0"})

        self.assertTrue(sent)
        note_on.assert_called_once_with(trigger.midi.MIDI_CHANNEL, 36, 90)
        note_off.assert_called_once_with(trigger.midi.MIDI_CHANNEL, 36)


class TempoTests(unittest.TestCase):
    def test_set_clamps_and_persists_the_shared_tempo(self):
        original_db_path = storage.DB_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            storage.DB_PATH = Path(temp_dir) / "test.db"
            try:
                storage.init_db()
                tempo.set(300)

                self.assertEqual(tempo.get(), tempo.MAX_BPM)
                self.assertEqual(storage.get_settings()["sequencer_bpm"], "240.0")
            finally:
                storage.DB_PATH = original_db_path

    def test_register_tap_derives_bpm_from_the_average_interval(self):
        original_taps = list(tempo._tap_times)
        try:
            tempo._tap_times = []
            self.assertIsNone(tempo.register_tap(now=0.0))
            self.assertEqual(tempo.register_tap(now=0.5), 120.0)
            self.assertEqual(tempo.register_tap(now=1.0), 120.0)
        finally:
            tempo._tap_times = original_taps

    def test_register_tap_restarts_after_a_pause_longer_than_the_window(self):
        original_taps = list(tempo._tap_times)
        try:
            tempo._tap_times = []
            tempo.register_tap(now=0.0)
            tempo.register_tap(now=0.5)

            self.assertIsNone(tempo.register_tap(now=5.0))
            self.assertEqual(tempo._tap_times, [5.0])
        finally:
            tempo._tap_times = original_taps

    def test_register_tap_clamps_to_the_supported_bpm_range(self):
        original_taps = list(tempo._tap_times)
        try:
            tempo._tap_times = []
            tempo.register_tap(now=0.0)

            self.assertEqual(tempo.register_tap(now=0.01), tempo.MAX_BPM)
        finally:
            tempo._tap_times = original_taps


class MetronomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_beat_uses_the_accent_note_on_the_dedicated_output(self):
        beat_received = asyncio.Event()

        async def on_beat(beat):
            self.assertEqual(beat, 0)
            beat_received.set()

        metronome.set_signature("4_4")
        with patch("engine.metronome.midi.metronome_note_on", return_value=True) as note_on:
            try:
                await metronome.start(on_beat)
                await asyncio.wait_for(beat_received.wait(), timeout=0.5)
            finally:
                await metronome.stop()

        note_on.assert_called_with(
            metronome.midi.MIDI_CHANNEL,
            metronome_sounds.ACCENT_NOTE,
            100,
        )
        self.assertFalse(metronome.get_state()["running"])

    async def test_clock_does_not_replay_missed_beats_in_a_burst(self):
        timestamps = []
        third_beat = asyncio.Event()

        async def on_beat(beat):
            if beat == 0:
                await asyncio.sleep(0.6)
            elif beat == 2:
                third_beat.set()

        def record_note(*_args):
            timestamps.append(asyncio.get_running_loop().time())
            return True

        metronome.set_signature("4_4")
        with (
            patch("engine.metronome.midi.metronome_note_on", side_effect=record_note),
            patch("engine.metronome.tempo.get", return_value=240.0),
        ):
            try:
                await metronome.start(on_beat)
                await asyncio.wait_for(third_beat.wait(), timeout=1.5)
            finally:
                await metronome.stop()

        self.assertGreaterEqual(timestamps[2] - timestamps[1], 0.2)

    def test_each_sound_style_generates_accent_and_normal_samples(self):
        original_generated_dir = metronome_sounds.GENERATED_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            metronome_sounds.GENERATED_DIR = Path(temp_dir)
            try:
                for style in metronome_sounds.STYLES:
                    sfz_path = metronome_sounds.write_metronome_sfz(style)
                    sfz = sfz_path.read_text()
                    self.assertIn(f"key={metronome_sounds.ACCENT_NOTE}", sfz)
                    self.assertIn(f"key={metronome_sounds.NORMAL_NOTE}", sfz)
                    self.assertTrue((Path(temp_dir) / f"{style}_accent.wav").exists())
                    self.assertTrue((Path(temp_dir) / f"{style}_normal.wav").exists())
            finally:
                metronome_sounds.GENERATED_DIR = original_generated_dir


class SampleFolderTests(unittest.TestCase):
    def test_normalize_folder_preserves_a_nested_relative_path(self):
        self.assertEqual(storage.normalize_folder("Pack/Drums/Kicks"), "Pack/Drums/Kicks")

    def test_normalize_folder_rejects_directory_traversal(self):
        with self.assertRaises(ValueError):
            storage.normalize_folder("Pack/../outside")


class LooperOverdubTests(unittest.TestCase):
    def setUp(self):
        self._reset_looper()

    def tearDown(self):
        self._reset_looper()

    @staticmethod
    def _reset_looper():
        looper._state = "stopped"
        looper._events = []
        looper._overdub_events = []
        looper._loop_duration = None
        looper._loop_start = None
        looper._record_start = None
        looper._started_at = None
        looper._task = None

    def test_overdub_start_is_a_no_op_unless_a_loop_is_already_playing(self):
        looper.overdub_start()
        self.assertEqual(looper.get_state()["state"], "stopped")

    def test_overdub_merges_new_hits_at_their_wrapped_loop_offset(self):
        looper._state = "playing"
        looper._events = [{"offset": 0.1, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0
        looper._loop_start = time.monotonic() - 2.5  # 0.5s into the second cycle

        looper.overdub_start()
        self.assertEqual(looper.get_state()["state"], "overdubbing")

        looper.record_event(2, 80)
        self.assertEqual(looper.get_state()["overdub_event_count"], 1)
        self.assertEqual(len(looper._events), 1)  # not merged into the live loop yet

        looper.overdub_stop()

        self.assertEqual(looper.get_state()["state"], "playing")
        self.assertEqual(looper.get_state()["overdub_event_count"], 0)
        pad_numbers = {e["pad_number"] for e in looper._events}
        self.assertEqual(pad_numbers, {1, 2})
        new_event = next(e for e in looper._events if e["pad_number"] == 2)
        self.assertAlmostEqual(new_event["offset"], 0.5, delta=0.05)

    def test_record_event_is_ignored_outside_recording_and_overdubbing(self):
        looper._state = "playing"
        looper.record_event(1, 100)
        self.assertEqual(looper._events, [])


class LooperPlayToggleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        LooperOverdubTests._reset_looper()

    def tearDown(self):
        LooperOverdubTests._reset_looper()

    async def test_play_start_resumes_a_stopped_loop_with_events(self):
        looper._state = "stopped"
        looper._events = [{"offset": 0.1, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0

        with patch("engine.looper.trigger.trigger_pad", new=AsyncMock()):
            await looper.play_start([], {})

            self.assertEqual(looper.get_state()["state"], "playing")
            self.assertIsNotNone(looper._task)

            looper._task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await looper._task

    async def test_play_start_is_a_no_op_without_a_recorded_loop(self):
        looper._state = "stopped"

        await looper.play_start([], {})

        self.assertEqual(looper.get_state()["state"], "stopped")
        self.assertIsNone(looper._task)


if __name__ == "__main__":
    unittest.main()

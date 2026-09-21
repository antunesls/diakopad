import asyncio
import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import storage
from engine import knob_registry, looper, metronome, metronome_sounds, sequencer, tempo, trigger


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
        looper._tracks = [looper.Track() for _ in range(looper.TRACK_COUNT)]
        looper._selected = 0
        looper._loop_duration = None
        looper._loop_start = None
        looper._record_start = None
        looper._started_at = None
        looper._task = None
        looper._playback_args = None

    def test_overdub_start_is_a_no_op_unless_the_armed_track_is_playing(self):
        looper.overdub_start()
        self.assertEqual(looper.get_state()["state"], "stopped")

    def test_overdub_merges_new_hits_at_their_wrapped_loop_offset(self):
        track = looper._tracks[0]
        track.state = "playing"
        track.events = [{"offset": 0.1, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0
        looper._loop_start = time.monotonic() - 2.5  # 0.5s into the second cycle

        looper.overdub_start()
        self.assertEqual(looper.get_state()["state"], "overdubbing")

        looper.record_event(2, 80)
        self.assertEqual(looper.get_state()["tracks"][0]["overdub_event_count"], 1)
        self.assertEqual(len(track.events), 1)  # not merged into the live loop yet

        looper.overdub_stop()

        self.assertEqual(looper.get_state()["tracks"][0]["state"], "playing")
        self.assertEqual(looper.get_state()["tracks"][0]["overdub_event_count"], 0)
        pad_numbers = {e["pad_number"] for e in track.events}
        self.assertEqual(pad_numbers, {1, 2})
        new_event = next(e for e in track.events if e["pad_number"] == 2)
        self.assertAlmostEqual(new_event["offset"], 0.5, delta=0.05)

    def test_record_event_is_ignored_outside_recording_and_overdubbing(self):
        track = looper._tracks[0]
        track.state = "playing"
        looper.record_event(1, 100)
        self.assertEqual(track.events, [])


class LooperQuantizeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        LooperOverdubTests._reset_looper()

    def tearDown(self):
        LooperOverdubTests._reset_looper()

    def test_quantize_to_bar_rounds_to_the_nearest_whole_bar(self):
        settings = {"sequencer_bpm": "120", "metronome_signature": "4_4"}
        # bar = 4 pulses * 60/120 = 2.0s
        self.assertAlmostEqual(looper._quantize_to_bar(3.1, settings), 4.0)  # rounds up to 2 bars
        self.assertAlmostEqual(looper._quantize_to_bar(0.9, settings), 2.0)  # rounds down to 1 bar
        self.assertAlmostEqual(looper._quantize_to_bar(2.0, settings), 2.0)  # exact bar, unchanged

    def test_quantize_to_bar_never_rounds_down_to_zero(self):
        settings = {"sequencer_bpm": "120", "metronome_signature": "4_4"}
        self.assertAlmostEqual(looper._quantize_to_bar(0.05, settings), 2.0)  # min 1 bar

    def test_quantize_to_bar_respects_the_time_signature_pulse_count(self):
        settings = {"sequencer_bpm": "120", "metronome_signature": "3_4"}
        # bar = 3 pulses * 60/120 = 1.5s
        self.assertAlmostEqual(looper._quantize_to_bar(1.6, settings), 1.5)

    def test_quantize_to_bar_falls_back_to_raw_duration_on_bad_bpm(self):
        self.assertEqual(looper._quantize_to_bar(3.1, {"sequencer_bpm": "not-a-number"}), 3.1)
        self.assertEqual(looper._quantize_to_bar(3.1, {"sequencer_bpm": "0"}), 3.1)

    async def test_record_stop_snaps_loop_duration_to_a_bar_when_enabled(self):
        settings = {
            "sequencer_bpm": "120", "metronome_signature": "4_4",
            "looper_quantize_enabled": "1",
        }
        track = looper._tracks[0]
        track.state = "recording"
        track.events = [{"offset": 0.05, "pad_number": 1, "velocity": 100}]
        looper._record_start = time.monotonic() - 3.1  # ~1.55 bars held

        with patch("engine.trigger.trigger_pad", new=AsyncMock()):
            await looper.record_stop([], settings)

        self.assertEqual(looper.get_state()["state"], "playing")
        self.assertAlmostEqual(looper._loop_duration, 4.0, delta=0.05)  # snapped to 2 bars
        await self._cancel_task()

    async def test_record_stop_never_places_a_late_hit_outside_the_quantized_cycle(self):
        # At 120 BPM in 4/4 each bar is 2s. The old nearest-bar rounding
        # chose 2s for this 2.9s take, leaving the 2.7s hit beyond the cycle.
        # Playback then waited past the cycle end and the next cycle started
        # late, audibly falling behind the beat.
        settings = {
            "sequencer_bpm": "120", "metronome_signature": "4_4",
            "looper_quantize_enabled": "1",
        }
        track = looper._tracks[0]
        track.state = "recording"
        track.events = [{"offset": 2.7, "pad_number": 1, "velocity": 100}]
        looper._record_start = time.monotonic() - 2.9

        with patch("engine.trigger.trigger_pad", new=AsyncMock()):
            await looper.record_stop([], settings)

        self.assertAlmostEqual(looper._loop_duration, 4.0, delta=0.05)
        await self._cancel_task()

    async def test_record_stop_keeps_raw_duration_when_disabled(self):
        settings = {
            "sequencer_bpm": "120", "metronome_signature": "4_4",
            "looper_quantize_enabled": "0",
        }
        track = looper._tracks[0]
        track.state = "recording"
        track.events = [{"offset": 0.05, "pad_number": 1, "velocity": 100}]
        looper._record_start = time.monotonic() - 3.1

        with patch("engine.trigger.trigger_pad", new=AsyncMock()):
            await looper.record_stop([], settings)

        self.assertAlmostEqual(looper._loop_duration, 3.1, delta=0.05)  # untouched
        await self._cancel_task()

    @staticmethod
    async def _cancel_task():
        task = looper._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class LooperPlayToggleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        LooperOverdubTests._reset_looper()

    def tearDown(self):
        LooperOverdubTests._reset_looper()

    async def test_play_start_resumes_a_stopped_loop_with_events(self):
        track = looper._tracks[0]
        track.state = "stopped"
        track.events = [{"offset": 0.1, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0

        with patch("engine.looper.trigger.trigger_pad", new=AsyncMock()):
            await looper.play_start([], {})

            self.assertEqual(looper.get_state()["state"], "playing")
            self.assertIsNotNone(looper._task)

            looper._task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await looper._task

    async def test_play_start_is_a_no_op_without_a_recorded_loop(self):
        looper._tracks[0].state = "stopped"

        await looper.play_start([], {})

        self.assertEqual(looper.get_state()["state"], "stopped")
        self.assertIsNone(looper._task)


class LooperTracksTests(unittest.IsolatedAsyncioTestCase):
    """The multi-track layer: shared cycle, per-track routing, mute/volume
    gain, clear isolation and selection guards."""

    def setUp(self):
        LooperOverdubTests._reset_looper()

    def tearDown(self):
        LooperOverdubTests._reset_looper()

    async def _cancel_task(self):
        task = looper._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_second_track_records_relative_to_the_cycle_and_inherits_its_length(self):
        first = looper._tracks[0]
        first.state = "playing"
        first.events = [{"offset": 0.0, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0
        looper._loop_start = time.monotonic() - 0.5  # 0.5s into the running cycle

        looper._selected = 1
        looper.record_start()
        self.assertEqual(looper._tracks[1].state, "recording")

        looper.record_event(3, 90)
        offset = looper._tracks[1].events[0]["offset"]
        self.assertAlmostEqual(offset, 0.5, delta=0.05)  # cycle-relative, not take-relative

        with patch("engine.trigger.trigger_pad", new=AsyncMock()):
            await looper.record_stop([], {"looper_quantize_enabled": "1", "sequencer_bpm": "120"})
            await self._cancel_task()

        self.assertEqual(looper._tracks[1].state, "playing")
        self.assertAlmostEqual(looper._loop_duration, 2.0, delta=0.05)  # 1st track fixed it; 2nd only inherits

    async def test_closing_the_first_take_starts_the_shared_cycle(self):
        track = looper._tracks[0]
        looper.record_start()
        track.events = [{"offset": 0.05, "pad_number": 2, "velocity": 100}]

        with patch("engine.trigger.trigger_pad", new=AsyncMock()):
            await looper.record_stop([], {"looper_quantize_enabled": "0"})
            self.assertEqual(looper.get_state()["state"], "playing")
            self.assertIsNotNone(looper._task)
            await self._cancel_task()

    async def test_closing_an_empty_takes_the_track_back_to_stopped(self):
        looper.record_start()

        with patch("engine.trigger.trigger_pad", new=AsyncMock()):
            await looper.record_stop([], {})

        self.assertEqual(looper._tracks[0].state, "stopped")
        self.assertIsNone(looper._loop_duration)

    def test_merged_events_skip_stopped_and_recording_tracks(self):
        stopped, recording, playing = looper._tracks[0], looper._tracks[1], looper._tracks[2]
        stopped.state = "stopped"
        stopped.events = [{"offset": 0.0, "pad_number": 1, "velocity": 100}]
        recording.state = "recording"
        recording.events = [{"offset": 0.1, "pad_number": 2, "velocity": 100}]
        playing.state = "playing"
        playing.events = [{"offset": 0.2, "pad_number": 3, "velocity": 100}]
        overdubbing = looper._tracks[3]
        overdubbing.state = "overdubbing"
        overdubbing.events = [{"offset": 0.05, "pad_number": 4, "velocity": 100}]

        merged = looper._merged_events()

        self.assertEqual([m[1] for m in merged], [4, 3])  # cycle-sorted, active tracks only

    def test_track_gain_applies_volume_and_mute_at_fire_time(self):
        track = looper.Track()
        self.assertEqual(looper._apply_track_gain(track, 100), 100)
        track.volume = 50
        self.assertEqual(looper._apply_track_gain(track, 100), 50)
        track.muted = True
        self.assertEqual(looper._apply_track_gain(track, 100), 0)
        track.muted = False
        track.volume = 0
        self.assertEqual(looper._apply_track_gain(track, 100), 0)  # volume 0 is silent

    def test_set_volume_clamps_into_the_0_100_range(self):
        looper.set_volume(0, 150)
        self.assertEqual(looper._tracks[0].volume, 100)
        looper.set_volume(0, -10)
        self.assertEqual(looper._tracks[0].volume, 0)
        looper.set_volume(0, 42.6)
        self.assertEqual(looper._tracks[0].volume, 43)

    def test_clear_track_only_erases_its_own_track(self):
        first, second = looper._tracks[0], looper._tracks[1]
        first.state = "playing"
        first.events = [{"offset": 0.0, "pad_number": 1, "velocity": 100}]
        second.state = "playing"
        second.events = [{"offset": 0.1, "pad_number": 2, "velocity": 100}]
        looper._loop_duration = 2.0

        looper.clear_track(0)

        self.assertEqual(first.state, "stopped")
        self.assertEqual(first.events, [])
        self.assertEqual(second.state, "playing")
        self.assertEqual(len(second.events), 1)
        self.assertAlmostEqual(looper._loop_duration, 2.0)  # other tracks still hold the cycle

    async def test_clearing_the_last_track_with_content_stops_the_cycle(self):
        track = looper._tracks[2]
        track.state = "playing"
        track.events = [{"offset": 0.0, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0

        looper.clear_track(2)

        self.assertEqual(looper.get_state()["state"], "stopped")
        self.assertIsNone(looper._loop_duration)

    def test_select_track_is_ignored_while_the_armed_track_is_capturing(self):
        looper._tracks[0].state = "recording"

        looper.select_track(1)
        self.assertEqual(looper.get_selected(), 0)  # must not move mid-take

        looper._tracks[0].state = "stopped"
        looper.select_track(1)
        self.assertEqual(looper.get_selected(), 1)

        looper.select_track(99)  # out of range
        self.assertEqual(looper.get_selected(), 1)

    async def test_record_start_auto_resumes_a_stopped_cycle(self):
        track = looper._tracks[0]
        track.state = "stopped"
        track.events = [{"offset": 0.0, "pad_number": 1, "velocity": 100}]
        looper._loop_duration = 2.0
        looper._playback_args = ([], {})

        looper._selected = 1
        looper.record_start()

        self.assertEqual(track.state, "playing")  # the cycle came back before the take
        self.assertIsNotNone(looper._task)
        self.assertEqual(looper._tracks[1].state, "recording")

        await self._cancel_task()

    def test_live_hits_are_routed_to_the_armed_track(self):
        looper._selected = 2
        looper._tracks[2].state = "recording"
        looper._record_start = time.monotonic() - 0.1

        looper.record_event(5, 110)

        self.assertEqual(len(looper._tracks[2].events), 1)
        self.assertTrue(all(len(t.events) == 0 for i, t in enumerate(looper._tracks) if i != 2))


class KnobSteppedCurveTests(unittest.TestCase):
    """The looper knob targets map a continuous CC sweep onto discrete
    stops (engine/knob_registry.py curve="stepped")."""

    @staticmethod
    def _meta(param):
        return next(m for m in knob_registry.GLOBAL_PARAMS if m["param"] == param)

    def test_looper_track_param_sweeps_the_knob_across_tracks_1_to_4(self):
        meta = self._meta("looper_track")
        self.assertEqual(knob_registry.cc_to_value(0, meta), 1)
        self.assertEqual(knob_registry.cc_to_value(32, meta), 2)
        self.assertEqual(knob_registry.cc_to_value(64, meta), 3)
        self.assertEqual(knob_registry.cc_to_value(127, meta), 4)

    def test_looper_mute_param_splits_the_knob_sweep_in_half(self):
        meta = self._meta("looper_mute")
        self.assertEqual(knob_registry.cc_to_value(0, meta), 0)
        self.assertEqual(knob_registry.cc_to_value(63, meta), 0)
        self.assertEqual(knob_registry.cc_to_value(64, meta), 1)
        self.assertEqual(knob_registry.cc_to_value(127, meta), 1)


if __name__ == "__main__":
    unittest.main()

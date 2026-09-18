import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
from engine import metronome, metronome_sounds, sequencer, tempo, trigger


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


class MetronomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_beat_uses_the_accent_note_on_the_dedicated_output(self):
        beat_received = asyncio.Event()

        async def on_beat(beat):
            self.assertEqual(beat, 0)
            beat_received.set()

        metronome.set_beats_per_bar(4)
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

        metronome.set_beats_per_bar(4)
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


if __name__ == "__main__":
    unittest.main()

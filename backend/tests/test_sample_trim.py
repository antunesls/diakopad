import array
import math
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import sample_trim
import sfz
import storage

SR = 44100


def write_wav(path: Path, segments: list[tuple[float, float]]) -> None:
    """segments: (seconds, amplitude 0..1) of a 1 kHz tone; amplitude 0 = silence."""
    data = array.array("h")
    t = 0
    for seconds, amp in segments:
        for _ in range(int(seconds * SR)):
            data.append(int(amp * 32767 * math.sin(2 * math.pi * 1000 * t / SR)))
            t += 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_leading_silence_is_skipped_keeping_a_pre_roll(self):
        path = self.dir / "rev.wav"
        write_wav(path, [(1.0, 0.0), (0.5, 0.8), (0.5, 0.0)])
        start, end = sample_trim.analyze(path)
        expected = SR - int(sample_trim.PRE_ROLL_MS * SR / 1000)
        self.assertAlmostEqual(start, expected, delta=int(0.001 * SR))
        self.assertAlmostEqual(end, int(1.5 * SR + sample_trim.TAIL_MS * SR / 1000), delta=int(0.002 * SR))

    def test_quiet_crescendo_is_not_mistaken_for_silence(self):
        path = self.dir / "swell.wav"
        # -50 dB below the peak is still sound (above the -60 dB threshold)
        write_wav(path, [(0.5, 0.8 * 10 ** (-50 / 20)), (0.5, 0.8)])
        start, _ = sample_trim.analyze(path)
        self.assertEqual(start, 0)

    def test_sound_that_starts_immediately_is_left_alone(self):
        path = self.dir / "kick.wav"
        write_wav(path, [(0.3, 0.9)])
        self.assertEqual(sample_trim.analyze(path), (0, None))

    def test_unreadable_file_is_not_trimmed(self):
        path = self.dir / "broken.wav"
        path.write_bytes(b"not audio")
        with patch.object(sample_trim, "_read_ffmpeg", return_value=None):
            self.assertEqual(sample_trim.analyze(path), (0, None))


class TrimCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = patch.object(storage, "DB_PATH", Path(self._tmp.name) / "t.db")
        p.start()
        self.addCleanup(p.stop)
        storage.init_db()
        self.samples = Path(self._tmp.name)
        write_wav(self.samples / "rev.wav", [(0.5, 0.0), (0.2, 0.8)])
        self.sid = storage.add_sample("rev.wav", "Rev")

    def test_analysis_is_cached_and_reused(self):
        rows = [{"sample_id": self.sid, "filename": "rev.wav"}]
        with patch.object(sample_trim, "analyze", wraps=sample_trim.analyze) as analyze:
            first = sample_trim.trims_for(rows, self.samples)
            second = sample_trim.trims_for(rows, self.samples)
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(first, second)
        self.assertGreater(first[self.sid][0], 0)

    def test_new_trim_version_triggers_reanalysis(self):
        rows = [{"sample_id": self.sid, "filename": "rev.wav"}]
        sample_trim.trims_for(rows, self.samples)
        with patch.object(sample_trim, "TRIM_VERSION", sample_trim.TRIM_VERSION + 1), \
                patch.object(sample_trim, "analyze", return_value=(7, None)) as analyze:
            self.assertEqual(sample_trim.trims_for(rows, self.samples)[self.sid], (7, None))
        analyze.assert_called_once()


class SfzTrimOpcodeTests(unittest.TestCase):
    VARIANT = {"filename": "rev.wav", "volume_db": 0, "pan": 0, "cutoff_hz": None,
               "trim_start": 47000, "trim_end": 120000}

    def test_bank_regions_get_offset_and_end_only_when_enabled(self):
        on = sfz.render_pad_bank(1, 36, [self.VARIANT], {"auto_trim": "1"})
        self.assertIn("offset=47000", on)
        self.assertIn("end=120000", on)
        off = sfz.render_pad_bank(1, 36, [self.VARIANT], {"auto_trim": "0"})
        self.assertNotIn("offset=", off)
        self.assertNotIn("end=", off)

    def test_untrimmed_variant_has_no_trim_opcodes(self):
        text = sfz.render_pad_bank(1, 36, [{**self.VARIANT, "trim_start": 0, "trim_end": None}], {})
        self.assertNotIn("offset=", text)
        self.assertNotIn("end=", text)


if __name__ == "__main__":
    unittest.main()

import unittest

import sfz


class RenderPadSfzTests(unittest.TestCase):
    PAD = {"filename": "kick.wav", "midi_note": 36, "volume_db": -2.0, "pan": 10, "cutoff_hz": None}

    def _global(self, settings):
        return next(line for line in sfz.render_pad_sfz(self.PAD, settings).splitlines() if line.startswith("<global>"))

    def test_voice_cap_and_min_attack_are_always_set(self):
        for settings in ({}, {"sustain_mode": "0", "velocity_sensitive": "1"}):
            line = self._global(settings)
            self.assertIn(f"note_polyphony={sfz.NOTE_POLYPHONY}", line)
            self.assertIn("ampeg_attack=0.0005", line)

    def test_sustain_and_velocity_settings_still_apply(self):
        self.assertIn("loop_mode=one_shot", self._global({}))
        self.assertIn("amp_veltrack=0", self._global({}))
        line = self._global({"sustain_mode": "0", "velocity_sensitive": "1"})
        self.assertIn("ampeg_release=0.05", line)
        self.assertIn("amp_veltrack=100", line)

    def test_region_keeps_note_volume_and_pan(self):
        region = next(line for line in sfz.render_pad_sfz(self.PAD, {}).splitlines() if line.startswith("<region>"))
        for opcode in ("key=36", "pitch_keycenter=36", "volume=-2.0", "pan=10"):
            self.assertIn(opcode, region)


if __name__ == "__main__":
    unittest.main()

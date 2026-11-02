import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import pwclock

CONF = """context.properties = {
    default.clock.rate          = 44100
    default.clock.allowed-rates = [ 44100 ]
    default.clock.quantum       = 256
    default.clock.min-quantum   = 256
    default.clock.max-quantum   = 256
}
"""


def metadata(rate: int, quantum: int) -> str:
    return (
        "Found \"settings\" metadata 33\n"
        f"update: id:0 key:'clock.force-rate' value:'{rate}' type:''\n"
        f"update: id:0 key:'clock.force-quantum' value:'{quantum}' type:''\n"
    )


class FakePwMetadata:
    def __init__(self, rate: int, quantum: int):
        self.state = {"clock.force-rate": rate, "clock.force-quantum": quantum}
        self.writes = []

    def __call__(self, *args):
        if len(args) == 1:
            return metadata(self.state["clock.force-rate"], self.state["clock.force-quantum"])
        _, key, value = args
        self.writes.append((key, int(value)))
        self.state[key] = int(value)
        return ""


class PwClockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conf = Path(self._tmp.name) / "99-diakopad-clock.conf"
        self.conf.write_text(CONF)
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIAKOPAD_CLOCK")}
        for p in (
            patch.dict(os.environ, env, clear=True),
            patch.object(pwclock, "CLOCK_CONF", self.conf),
            patch.object(pwclock, "available", return_value=True),
            patch.object(pwclock, "gave_up", False),
            patch.object(pwclock, "_rate_restores", []),
        ):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_wanted_clock_comes_from_installer_conf(self):
        self.assertEqual(pwclock.wanted_clock(), (44100, 256))

    def test_env_overrides_and_off_switch(self):
        with patch.dict(os.environ, {"DIAKOPAD_CLOCK_QUANTUM": "128"}):
            self.assertEqual(pwclock.wanted_clock(), (44100, 128))
        with patch.dict(os.environ, {"DIAKOPAD_CLOCK": "off"}):
            self.assertIsNone(pwclock.wanted_clock())

    def test_without_installer_conf_the_clock_is_left_alone(self):
        self.conf.unlink()
        self.assertIsNone(pwclock.wanted_clock())
        fake = FakePwMetadata(48000, 1024)
        with patch.object(pwclock, "_run", fake):
            self.assertFalse(pwclock.enforce()["changed"])
        self.assertEqual(fake.writes, [])

    def test_widget_override_is_restored_and_flags_rate_change(self):
        fake = FakePwMetadata(48000, 1024)  # what Ubuntu Studio's widget left behind
        with patch.object(pwclock, "_run", fake):
            result = pwclock.enforce()
        self.assertTrue(result["changed"])
        self.assertTrue(result["rate_changed"])
        self.assertEqual(fake.state, {"clock.force-rate": 44100, "clock.force-quantum": 256})

    def test_quantum_only_change_does_not_ask_for_engine_restart(self):
        fake = FakePwMetadata(44100, 1024)
        with patch.object(pwclock, "_run", fake):
            result = pwclock.enforce()
        self.assertTrue(result["changed"])
        self.assertFalse(result["rate_changed"])
        self.assertEqual(fake.writes, [("clock.force-quantum", 256)])

    def test_already_pinned_is_a_no_op(self):
        fake = FakePwMetadata(44100, 256)
        with patch.object(pwclock, "_run", fake):
            self.assertFalse(pwclock.enforce()["changed"])
        self.assertEqual(fake.writes, [])

    def test_gives_up_when_something_keeps_moving_the_rate(self):
        fake = FakePwMetadata(48000, 256)
        with patch.object(pwclock, "_run", fake):
            for _ in range(pwclock.MAX_RATE_RESTORES):
                self.assertTrue(pwclock.enforce()["rate_changed"])
                fake.state["clock.force-rate"] = 48000  # the other program wins again
            self.assertFalse(pwclock.enforce()["changed"])
        self.assertTrue(pwclock.gave_up)


if __name__ == "__main__":
    unittest.main()

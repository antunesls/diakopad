import unittest

from engine import transport


class TransportTests(unittest.TestCase):
    def test_tempo_change_preserves_the_current_musical_position(self):
        clock = transport.Transport(bpm=120, pulses_per_bar=4)
        clock.start(now=10.0)

        self.assertEqual(clock.tick_at(now=11.0), 192)
        clock.set_bpm(60, now=11.0)

        self.assertEqual(clock.tick_at(now=11.0), 192)
        self.assertEqual(clock.tick_at(now=12.0), 288)

    def test_next_bar_tick_aligns_to_the_shared_bar_boundary(self):
        clock = transport.Transport(bpm=120, pulses_per_bar=4)
        clock.start(now=0.0)

        self.assertEqual(clock.next_bar_tick(now=1.25), 384)
        self.assertEqual(clock.next_bar_tick(now=2.0), 384)


if __name__ == "__main__":
    unittest.main()

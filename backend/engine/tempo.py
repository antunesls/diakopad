"""Single shared global tempo (BPM) - the one source of truth used by the
step sequencer, the metronome, and the Knobs tab's "Tempo" global param.
Persisted under the `sequencer_bpm` settings key (kept from when only the
sequencer had a tempo, before the metronome needed to share it).
"""
from __future__ import annotations

import time
from typing import Optional

import storage
from engine import transport

MIN_BPM = 40.0
MAX_BPM = 240.0

# Tap-tempo window: taps more than TAP_RESET_SECONDS apart start a fresh
# measurement, and only the last TAP_MAX_TAPS are averaged - same algorithm
# the frontend's on-screen TAP buttons use, mirrored here so a physical MIDI
# button (controller action "tap_tempo") can tap the tempo server-side.
TAP_RESET_SECONDS = 2.0
TAP_MAX_TAPS = 6

_bpm: float = 100.0
_tap_times: list[float] = []


def get() -> float:
    return _bpm


def set(value: float, persist: bool = True) -> None:
    global _bpm
    _bpm = max(MIN_BPM, min(MAX_BPM, value))
    transport.set_bpm(_bpm)
    if persist:
        storage.set_setting("sequencer_bpm", str(_bpm))


def load() -> None:
    global _bpm
    _bpm = float(storage.get_settings().get("sequencer_bpm", 100))
    transport.set_bpm(_bpm)


def register_tap(now: Optional[float] = None) -> Optional[float]:
    """Registers one tap and returns the BPM derived from the average interval
    of the recent taps, or None while there aren't two taps in the window yet.
    `now` (monotonic seconds) is injectable for tests."""
    global _tap_times
    moment = time.monotonic() if now is None else now
    if _tap_times and moment - _tap_times[-1] > TAP_RESET_SECONDS:
        _tap_times = []
    _tap_times.append(moment)
    _tap_times = _tap_times[-TAP_MAX_TAPS:]
    if len(_tap_times) < 2:
        return None
    elapsed = _tap_times[-1] - _tap_times[0]
    if elapsed <= 0:
        return None
    bpm = round(60.0 / (elapsed / (len(_tap_times) - 1)))
    return max(MIN_BPM, min(MAX_BPM, float(bpm)))

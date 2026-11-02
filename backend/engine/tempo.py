"""Single shared global tempo (BPM) - the one source of truth used by the
step sequencer, the metronome, and the Knobs tab's "Tempo" global param.
Persisted under the `sequencer_bpm` settings key (kept from when only the
sequencer had a tempo, before the metronome needed to share it).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

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
# Called with the new BPM after every change (e.g. the shared delay bus
# re-times its echoes, see orchestrator). Must be cheap and non-blocking.
_listeners: list[Callable[[float], None]] = []


def add_listener(callback: Callable[[float], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def _notify() -> None:
    for callback in list(_listeners):
        try:
            callback(_bpm)
        except Exception:  # pragma: no cover - a listener must never break tempo changes
            import logging
            logging.getLogger("diakopad.engine.tempo").exception("tempo listener failed")


def get() -> float:
    return _bpm


def set(value: float, persist: bool = True) -> None:
    global _bpm
    _bpm = max(MIN_BPM, min(MAX_BPM, value))
    transport.set_bpm(_bpm)
    if persist:
        storage.set_setting("sequencer_bpm", str(_bpm))
    _notify()


def load() -> None:
    global _bpm
    _bpm = float(storage.get_settings().get("sequencer_bpm", 100))
    transport.set_bpm(_bpm)
    _notify()


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

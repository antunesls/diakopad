"""Single shared global tempo (BPM) - the one source of truth used by the
step sequencer, the metronome, and the Knobs tab's "Tempo" global param.
Persisted under the `sequencer_bpm` settings key (kept from when only the
sequencer had a tempo, before the metronome needed to share it).
"""
from __future__ import annotations

import storage

MIN_BPM = 40.0
MAX_BPM = 240.0

_bpm: float = 100.0


def get() -> float:
    return _bpm


def set(value: float, persist: bool = True) -> None:
    global _bpm
    _bpm = max(MIN_BPM, min(MAX_BPM, value))
    if persist:
        storage.set_setting("sequencer_bpm", str(_bpm))


def load() -> None:
    global _bpm
    _bpm = float(storage.get_settings().get("sequencer_bpm", 100))

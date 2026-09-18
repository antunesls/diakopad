"""Metronome: an independent click, sharing the app's global tempo
(engine/tempo.py) with the step sequencer but with its own play/stop - so
you can click along without necessarily running the sequencer's pattern -
its own click-sound style (engine/metronome_sounds.py), and its own time
signature (engine/time_signatures.py, which pulse count and accent points
to use for the current bar).

Same absolute-time-rescheduling clock approach as engine/sequencer.py, to
bound drift without pretending asyncio is a hard-real-time scheduler.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

import midi
from engine import metronome_sounds, tempo, time_signatures

_running = False
_beat_in_bar = 0
_signature = time_signatures.DEFAULT_SIGNATURE
_task: Optional[asyncio.Task] = None
_on_beat: Optional[Callable[[int], Awaitable[None]]] = None


def get_state() -> dict:
    return {"running": _running, "beat_in_bar": _beat_in_bar, "signature": _signature}


def set_signature(signature: str) -> None:
    global _signature, _beat_in_bar
    _signature = signature if signature in time_signatures.SIGNATURES else time_signatures.DEFAULT_SIGNATURE
    _beat_in_bar = 0


async def start(on_beat: Callable[[int], Awaitable[None]]) -> None:
    global _running, _task, _on_beat, _beat_in_bar
    if _running:
        return
    _running = True
    _beat_in_bar = 0
    _on_beat = on_beat
    _task = asyncio.create_task(_clock())


async def stop() -> None:
    global _running, _task, _beat_in_bar
    _running = False
    if _task is not None:
        _task.cancel()
        _task = None
    _beat_in_bar = 0


async def _clock() -> None:
    global _beat_in_bar
    next_tick = time.monotonic()
    try:
        while _running:
            meta = time_signatures.get(_signature)
            note = metronome_sounds.ACCENT_NOTE if _beat_in_bar in meta["accents"] else metronome_sounds.NORMAL_NOTE
            midi.metronome_note_on(midi.MIDI_CHANNEL, note, 100)
            if _on_beat is not None:
                await _on_beat(_beat_in_bar)
            _beat_in_bar = (_beat_in_bar + 1) % meta["pulses"]
            interval = 60.0 / tempo.get()
            next_tick += interval
            now = time.monotonic()
            if next_tick <= now:
                next_tick = now + interval
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
    except asyncio.CancelledError:
        pass

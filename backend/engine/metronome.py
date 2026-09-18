"""Metronome: an independent quarter-note click, sharing the app's global
tempo (engine/tempo.py) with the step sequencer but with its own play/stop
- so you can click along without necessarily running the sequencer's
pattern - and its own click-sound style (engine/metronome_sounds.py).

Same absolute-time-rescheduling clock approach as engine/sequencer.py, to
bound drift without pretending asyncio is a hard-real-time scheduler.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from engine import metronome_sounds, tempo, trigger

_running = False
_beat_in_bar = 0
_beats_per_bar = 4
_task: Optional[asyncio.Task] = None
_on_beat: Optional[Callable[[int], Awaitable[None]]] = None


def get_state() -> dict:
    return {"running": _running, "beat_in_bar": _beat_in_bar, "beats_per_bar": _beats_per_bar}


def set_beats_per_bar(n: int) -> None:
    global _beats_per_bar, _beat_in_bar
    _beats_per_bar = n
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
            note = metronome_sounds.ACCENT_NOTE if _beat_in_bar == 0 else metronome_sounds.NORMAL_NOTE
            await trigger.trigger_note(note)
            if _on_beat is not None:
                await _on_beat(_beat_in_bar)
            _beat_in_bar = (_beat_in_bar + 1) % _beats_per_bar
            next_tick += 60.0 / tempo.get()
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
    except asyncio.CancelledError:
        pass

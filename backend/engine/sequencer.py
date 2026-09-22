"""Step sequencer clock: one shared 16-step pattern, running at the app's
shared global tempo (engine/tempo.py - also used by the metronome).

State is split between SQLite (the pattern itself, persisted via storage.py
so it survives a restart) and an in-memory cache kept in sync on every
mutation - the clock loop reads only the cache, never SQLite, to keep
per-tick I/O out of the timing-sensitive path. Transport (running/
current_step) is in-memory only and resets to stopped on restart, same
spirit as app.py's _pending_learn.

Uses absolute-time rescheduling (next_tick += step_seconds, not a plain
cumulative sleep) to bound drift rather than let per-iteration overhead
accumulate - still not a hard-real-time scheduler (asyncio can't promise
that), but keeps a casual drum-machine feel acceptable under normal load.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

import storage
from engine import tempo, transport, trigger

STEP_COUNT = 16

_running = False
_current_step = 0
_pattern: dict[tuple[int, int], bool] = {}
_task: Optional[asyncio.Task] = None
_on_tick: Optional[Callable[[int], Awaitable[None]]] = None


def load_pattern(steps: list[dict]) -> None:
    global _pattern
    _pattern = {(s["pad_number"], s["step_index"]): bool(s["active"]) for s in steps}


def toggle_step(pad_number: int, step_index: int, active: bool) -> None:
    _pattern[(pad_number, step_index)] = active
    storage.set_sequencer_step(pad_number, step_index, active)


def clear() -> None:
    for key in list(_pattern):
        _pattern[key] = False
    storage.clear_sequencer_steps()


def get_state() -> dict:
    return {
        "running": _running,
        "current_step": _current_step,
        "steps": [
            {"pad_number": pad, "step_index": step, "active": active}
            for (pad, step), active in sorted(_pattern.items())
        ],
    }


async def start(pads: list[dict], settings: dict, on_tick: Callable[[int], Awaitable[None]]) -> None:
    global _running, _task, _on_tick
    if _running:
        return
    _running = True
    _on_tick = on_tick
    transport.set_bpm(tempo.get())
    transport.start()
    _task = asyncio.create_task(_clock(pads, settings))


async def stop() -> None:
    global _running, _task, _current_step
    _running = False
    if _task is not None:
        _task.cancel()
        _task = None
    _current_step = 0


async def _clock(pads: list[dict], settings: dict) -> None:
    global _current_step
    ticks_per_step = transport.TICKS_PER_BEAT // 4
    next_tick = transport.shared().next_grid_tick(ticks_per_step)
    try:
        while _running:
            delay = transport.seconds_until_tick(next_tick)
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -0.02:
                next_tick = transport.shared().next_grid_tick(ticks_per_step)
                continue
            _current_step = (next_tick // ticks_per_step) % STEP_COUNT
            for (pad_number, step_index), active in list(_pattern.items()):
                if active and step_index == _current_step:
                    await trigger.trigger_pad(pad_number, pads, settings=settings)
            if _on_tick is not None:
                await _on_tick(_current_step)
            next_tick += ticks_per_step
    except asyncio.CancelledError:
        pass

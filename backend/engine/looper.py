"""Live looper: ONE shared recording (not per-pad), like a classic loop
pedal - record whatever pads get hit while recording, stop fixes the loop's
length to however long that took (no tempo quantization for now), then it
repeats automatically. In-memory only (loop content doesn't need to survive
a server restart).

Once playing, an overdub pass layers more hits onto the SAME loop length
without restarting playback: the events already looping keep playing while
new hits are captured into a side buffer (so a `for event in _events` mid-
cycle in _playback_loop never sees a half-written list) and merged in when
the pass ends.

Recording is fed by app.py's live note-on observer (see midi.py/app.py's
_on_midi_note); playback reuses the same engine/trigger.py primitive as the
step sequencer, on its own absolute-time-rescheduled loop to bound drift.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from engine import trigger

_state = "stopped"  # "stopped" | "recording" | "playing" | "overdubbing"
_events: list[dict] = []  # [{"offset": float, "pad_number": int, "velocity": int}]
_overdub_events: list[dict] = []  # captured during an overdub pass, merged into _events on stop
_loop_duration: Optional[float] = None
_loop_start: Optional[float] = None  # monotonic timestamp of the current cycle's start
_record_start: Optional[float] = None
_started_at: Optional[float] = None
_task: Optional[asyncio.Task] = None


def get_state() -> dict:
    return {
        "state": _state,
        "loop_duration": _loop_duration,
        "event_count": len(_events),
        "overdub_event_count": len(_overdub_events),
        "started_at": _started_at,
    }


def record_start() -> None:
    global _state, _events, _record_start, _started_at
    _cancel_task()
    _events = []
    _record_start = time.monotonic()
    _started_at = time.time()
    _state = "recording"


def overdub_start() -> None:
    """Starts layering onto the loop that's already playing - the running
    _playback_loop task is left alone, so what's already recorded keeps
    sounding while this pass is captured."""
    global _state, _overdub_events, _started_at
    if _state != "playing":
        return
    _overdub_events = []
    _started_at = time.time()
    _state = "overdubbing"


def overdub_stop() -> None:
    global _state, _events, _overdub_events, _started_at
    if _state != "overdubbing":
        return
    _events = _events + _overdub_events
    _overdub_events = []
    _state = "playing"
    _started_at = time.time()  # re-anchor the elapsed-time reference, mid-cycle


def record_event(pad_number: int, velocity: int) -> None:
    if _state == "recording" and _record_start is not None:
        _events.append(
            {"offset": time.monotonic() - _record_start, "pad_number": pad_number, "velocity": velocity}
        )
    elif _state == "overdubbing" and _loop_start is not None and _loop_duration:
        offset = (time.monotonic() - _loop_start) % _loop_duration
        _overdub_events.append({"offset": offset, "pad_number": pad_number, "velocity": velocity})


_ACTIVE_STATES = ("playing", "overdubbing")


async def record_stop(pads: list[dict], settings: dict) -> None:
    global _state, _loop_duration, _started_at, _task
    if _state != "recording" or _record_start is None:
        return
    duration = time.monotonic() - _record_start
    if _events and duration > 0:
        _loop_duration = duration
        _state = "playing"
        _started_at = time.time()
        _task = asyncio.create_task(_playback_loop(pads, settings))
    else:
        _loop_duration = None
        _state = "stopped"


def stop() -> None:
    global _state, _overdub_events
    _cancel_task()
    _state = "stopped"
    _overdub_events = []


def clear() -> None:
    global _events, _loop_duration
    stop()
    _events = []
    _loop_duration = None


def _cancel_task() -> None:
    global _task, _loop_start
    if _task is not None:
        _task.cancel()
        _task = None
    _loop_start = None


async def _playback_loop(pads: list[dict], settings: dict) -> None:
    global _loop_start
    _loop_start = time.monotonic()
    try:
        while _state in _ACTIVE_STATES and _loop_duration:
            for event in sorted(_events, key=lambda e: e["offset"]):
                delay = (_loop_start + event["offset"]) - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                if _state not in _ACTIVE_STATES:
                    return
                await trigger.trigger_pad(event["pad_number"], pads, event["velocity"], settings)
            remaining = (_loop_start + _loop_duration) - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            _loop_start += _loop_duration
    except asyncio.CancelledError:
        pass

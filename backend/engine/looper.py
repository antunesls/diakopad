"""Live looper: ONE shared recording (not per-pad), like a classic loop
pedal - record whatever pads get hit while recording, stop fixes the loop's
length to however long that took (no tempo quantization for now), then it
repeats automatically. In-memory only (loop content doesn't need to survive
a server restart). Overdubbing a second take onto an already-looping
playback is intentionally out of scope for now.

Recording is fed by app.py's live note-on observer (see midi.py/app.py's
_on_midi_note); playback reuses the same engine/trigger.py primitive as the
step sequencer, on its own absolute-time-rescheduled loop to bound drift.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from engine import trigger

_state = "stopped"  # "stopped" | "recording" | "playing"
_events: list[dict] = []  # [{"offset": float, "pad_number": int, "velocity": int}]
_loop_duration: Optional[float] = None
_record_start: Optional[float] = None
_started_at: Optional[float] = None
_task: Optional[asyncio.Task] = None


def get_state() -> dict:
    return {
        "state": _state,
        "loop_duration": _loop_duration,
        "event_count": len(_events),
        "started_at": _started_at,
    }


def record_start() -> None:
    global _state, _events, _record_start, _started_at
    _cancel_task()
    _events = []
    _record_start = time.monotonic()
    _started_at = time.time()
    _state = "recording"


def record_event(pad_number: int, velocity: int) -> None:
    if _state != "recording" or _record_start is None:
        return
    _events.append({"offset": time.monotonic() - _record_start, "pad_number": pad_number, "velocity": velocity})


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
    global _state
    _cancel_task()
    _state = "stopped"


def clear() -> None:
    global _events, _loop_duration
    stop()
    _events = []
    _loop_duration = None


def _cancel_task() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


async def _playback_loop(pads: list[dict], settings: dict) -> None:
    loop_start = time.monotonic()
    try:
        while _state == "playing" and _loop_duration:
            for event in sorted(_events, key=lambda e: e["offset"]):
                delay = (loop_start + event["offset"]) - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                if _state != "playing":
                    return
                await trigger.trigger_pad(event["pad_number"], pads, event["velocity"], settings)
            remaining = (loop_start + _loop_duration) - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            loop_start += _loop_duration
    except asyncio.CancelledError:
        pass

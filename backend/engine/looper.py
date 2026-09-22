"""Live looper: FOUR synchronized tracks fed by pad hits, like a tabletop
loop station (RC-505 style) instead of the old single shared pedal loop.
In-memory only (loop content doesn't need to survive a server restart).

Design notes:

- All tracks share ONE global cycle. The first track to close a take fixes
  the cycle length (quantized to whole bars when looper_quantize_enabled);
  every later take records offsets RELATIVE to the running cycle and simply
  inherits that length, so tracks can never drift apart.

- ONE playback task schedules the merged events of every playing track
  against the same absolute-time _loop_start (same drift-bounding pattern
  as engine/sequencer.py). A track still recording is silent until its
  take closes - only "playing"/"overdubbing" tracks sound.

- Per-track volume scales each event's velocity at fire time, and mute is
  re-checked at fire time too, so both react mid-cycle (unmuting re-enters
  the loop where it already is instead of waiting for the next cycle).

- The SELECTED track is the armed one: record/overdub (UI buttons, REST
  endpoints and hardware toggles) all act on it, and live hits are routed
  to it by record_event. Selecting another track while the armed one is
  capturing is ignored - record_stop must never land on the wrong track.

- Overdub is still the side-buffer pattern: the running task is left alone
  while the pass is captured into track.overdub_events and merged in
  atomically when the pass ends (a mid-cycle `for` over merged events must
  never see a half-written list).

- Recording is fed by app.py's live note-on observer (see midi.py/app.py's
  _on_midi_note); playback reuses the same engine/trigger.py primitive as
  the step sequencer. Track selection and mute are also exposed as knob
  targets (engine/knob_registry.py global params looper_track/looper_mute).

- When the last track with content is cleared, global playback ends and
  the cycle resets; an open take on another track still counts as content,
  since more hits may land on the cycle at any moment.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Optional

from engine import tempo, time_signatures, transport, trigger

TRACK_COUNT = 4
DEFAULT_DUPLICATE_HIT_WINDOW_MS = 30
MAX_DUPLICATE_HIT_WINDOW_MS = 200

_ACTIVE_STATES = ("playing", "overdubbing")


class Track:
    """One looper track: its own events, overdub side buffer, state and mix
    (mute/volume). The cycle length is NOT per-track - it's the shared
    global _loop_duration, fixed by the first take that closes."""

    def __init__(self) -> None:
        self.events: list[dict] = []  # [{"offset": float, "pad_number": int, "velocity": int}]
        self.overdub_events: list[dict] = []  # captured during an overdub pass, merged into events on stop
        self.state: str = "stopped"  # "stopped" | "recording" | "playing" | "overdubbing"
        self.muted: bool = False
        self.volume: int = 100  # percent, scales velocity at fire time


_tracks: list[Track] = [Track() for _ in range(TRACK_COUNT)]
_selected: int = 0  # index of the armed track record/overdub act on
_loop_duration: Optional[float] = None  # shared cycle length, fixed by the first take
_loop_start: Optional[float] = None  # monotonic timestamp of the current cycle's start
_record_start: Optional[float] = None
_loop_length_ticks: Optional[int] = None
_loop_start_tick: Optional[int] = None
_record_start_tick: Optional[int] = None
_started_at: Optional[float] = None
_task: Optional[asyncio.Task] = None
_playback_args: Optional[tuple[list[dict], dict]] = None  # pads/settings reused by record_start's auto-resume
_last_recorded_event: Optional[tuple[int, int, float]] = None
_duplicate_hit_window_seconds = DEFAULT_DUPLICATE_HIT_WINDOW_MS / 1000.0


def get_state() -> dict:
    duration = _loop_duration
    if _loop_length_ticks is not None:
        duration = _loop_length_ticks * 60.0 / tempo.get() / transport.TICKS_PER_BEAT
    return {
        "state": _global_state(),
        "selected_track": _selected,
        "loop_duration": duration,
        "started_at": _started_at,
        "tracks": [
            {
                "state": t.state,
                "muted": t.muted,
                "volume": t.volume,
                "event_count": len(t.events),
                "overdub_event_count": len(t.overdub_events),
            }
            for t in _tracks
        ],
    }


def _global_state() -> str:
    """Keeps the old single-loop contract for UI labels and hardware
    toggles: what the armed track is doing, else whether the shared cycle
    is running."""
    selected = _tracks[_selected]
    if selected.state in ("recording", "overdubbing"):
        return selected.state
    if _task is not None and not _task.done() and _loop_duration:
        return "playing"
    return "stopped"


def get_selected() -> int:
    return _selected


def is_capturing() -> bool:
    """Whether live hits should be recorded right now (the armed track is
    recording or overdubbing) - app.py's note observer asks this instead of
    parsing get_state()."""
    return _tracks[_selected].state in ("recording", "overdubbing")


def select_track(index: int) -> None:
    global _selected
    if not 0 <= index < TRACK_COUNT:
        return
    if _tracks[_selected].state in ("recording", "overdubbing"):
        return  # finish the armed take first: record_stop must not land on another track
    _selected = index


def toggle_mute(index: int) -> None:
    if not 0 <= index < TRACK_COUNT:
        return
    _tracks[index].muted = not _tracks[index].muted


def set_mute(index: int, muted: bool) -> None:
    if not 0 <= index < TRACK_COUNT:
        return
    _tracks[index].muted = bool(muted)


def set_volume(index: int, volume: float) -> None:
    if not 0 <= index < TRACK_COUNT:
        return
    _tracks[index].volume = max(0, min(100, int(round(volume))))


def set_duplicate_hit_window(milliseconds: int | str) -> None:
    """Updates the recording filter from the persisted millisecond setting."""
    global _duplicate_hit_window_seconds
    try:
        value = int(milliseconds)
    except (TypeError, ValueError):
        value = DEFAULT_DUPLICATE_HIT_WINDOW_MS
    value = max(0, min(MAX_DUPLICATE_HIT_WINDOW_MS, value))
    _duplicate_hit_window_seconds = value / 1000.0


def record_start() -> None:
    global _record_start, _record_start_tick, _started_at, _task, _last_recorded_event
    track = _tracks[_selected]
    if track.state == "overdubbing":
        overdub_stop()  # merge the open pass before replacing the take
    if _loop_duration and (_task is None or _task.done()) and any(t.events for t in _tracks):
        # A cycle already exists but playback was stopped: resume it first so
        # this take records against the running cycle, never a dead anchor.
        for t in _tracks:
            if t.events:
                t.state = "playing"
        _started_at = time.time()
        if _playback_args is not None:
            _task = asyncio.create_task(_playback_loop(*_playback_args))
    track.events = []
    track.overdub_events = []
    track.state = "recording"
    _record_start = time.monotonic()
    transport_was_running = transport.is_running()
    transport.start()
    if transport_was_running and _loop_duration is None and _loop_length_ticks is None:
        _record_start_tick = transport.next_bar_tick()
    else:
        _record_start_tick = transport.tick_at()
    _last_recorded_event = None
    _started_at = time.time()


def overdub_start() -> None:
    global _started_at, _last_recorded_event
    track = _tracks[_selected]
    if track.state != "playing":
        return
    track.overdub_events = []
    track.state = "overdubbing"
    _last_recorded_event = None
    _started_at = time.time()


def overdub_stop() -> None:
    global _started_at
    track = _tracks[_selected]
    if track.state != "overdubbing":
        return
    track.events = track.events + track.overdub_events
    track.overdub_events = []
    track.state = "playing"
    _started_at = time.time()  # re-anchor the elapsed-time reference, mid-cycle


def record_event(pad_number: int, velocity: int) -> None:
    """Routes a live hit into the armed track. Takes recorded while a cycle
    is running store offsets relative to that cycle (modulo the loop
    length); the very first take is zero-based from its own start."""
    global _last_recorded_event
    track = _tracks[_selected]
    if track.state not in ("recording", "overdubbing"):
        return
    now = time.monotonic()
    last_event = _last_recorded_event
    if (
        last_event is not None
        and last_event[:2] == (pad_number, velocity)
        and now - last_event[2] < _duplicate_hit_window_seconds
    ):
        return
    _last_recorded_event = (pad_number, velocity, now)
    if track.state == "recording":
        current_tick = None
        if _record_start_tick is not None:
            current_tick = transport.tick_at()
            if current_tick < _record_start_tick:
                return
        if _loop_length_ticks and _loop_start_tick is not None:
            if current_tick is None:
                current_tick = transport.tick_at()
            offset = (current_tick - _loop_start_tick) % _loop_length_ticks
            track.events.append({"tick": offset, "pad_number": pad_number, "velocity": velocity})
            return
        if _loop_duration and _loop_start is not None:
            offset = (now - _loop_start) % _loop_duration
        elif _record_start is not None:
            offset = now - _record_start
        else:
            return
        track.events.append({"offset": offset, "pad_number": pad_number, "velocity": velocity})
    elif track.state == "overdubbing" and (
        (_loop_length_ticks is not None and _loop_start_tick is not None)
        or (_loop_start is not None and _loop_duration)
    ):
        if _loop_length_ticks and _loop_start_tick is not None:
            offset = (transport.tick_at() - _loop_start_tick) % _loop_length_ticks
            track.overdub_events.append({"tick": offset, "pad_number": pad_number, "velocity": velocity})
            return
        offset = (now - _loop_start) % _loop_duration
        track.overdub_events.append({"offset": offset, "pad_number": pad_number, "velocity": velocity})


async def record_stop(pads: list[dict], settings: dict) -> None:
    global _loop_duration, _loop_length_ticks, _record_start, _record_start_tick, _started_at, _task, _last_recorded_event
    track = _tracks[_selected]
    if track.state != "recording" or _record_start is None:
        return
    held = time.monotonic() - _record_start
    _record_start = None
    _last_recorded_event = None
    if not track.events:
        track.state = "stopped"
        _record_start_tick = None
        _maybe_stop_playback()
        return
    if _loop_duration is None and _loop_length_ticks is None and _record_start_tick is not None:
        held_ticks = max(1, transport.tick_at() - _record_start_tick)
        bar_ticks = transport.shared().ticks_per_bar()
        if settings.get("looper_quantize_enabled", "1") == "1":
            loop_ticks = max(bar_ticks, round(held_ticks / bar_ticks) * bar_ticks)
        else:
            loop_ticks = held_ticks
        last_event_tick = max(event.get("tick", 0) for event in track.events)
        if settings.get("looper_quantize_enabled", "1") == "1" and loop_ticks <= last_event_tick:
            loop_ticks = (last_event_tick // bar_ticks + 1) * bar_ticks
        _loop_length_ticks = loop_ticks
        _loop_duration = loop_ticks * 60.0 / tempo.get() / transport.TICKS_PER_BEAT
    elif _loop_duration is None:
        # First take to close: it fixes the shared cycle length.
        duration = held
        if settings.get("looper_quantize_enabled", "1") == "1":
            last_event_offset = max(event["offset"] for event in track.events)
            duration = _quantize_to_bar(duration, settings, last_event_offset)
        _loop_duration = duration
    _record_start_tick = None
    track.state = "playing"
    _started_at = time.time()
    _playback_args = (pads, settings)
    if _task is None or _task.done():
        _task = asyncio.create_task(_playback_loop(pads, settings))


def stop() -> None:
    """Stops the whole cycle (all tracks) but keeps every track's events -
    play_start resumes them; clear/clear_track are what erase content."""
    global _record_start, _last_recorded_event
    _cancel_task()
    _record_start = None
    _last_recorded_event = None
    for track in _tracks:
        track.overdub_events = []
        track.state = "stopped"


async def play_start(pads: list[dict], settings: dict) -> None:
    """Resumes the shared cycle after a stop() (which keeps the events);
    clear() is what actually erases them."""
    global _started_at, _task
    if is_capturing():
        return
    if not _loop_duration or not any(t.events for t in _tracks):
        return
    if _task is not None and not _task.done():
        return
    for track in _tracks:
        if track.events:
            track.state = "playing"
    _started_at = time.time()
    _playback_args = (pads, settings)
    _task = asyncio.create_task(_playback_loop(pads, settings))


def clear() -> None:
    """Erases everything - all tracks' events AND their mix settings."""
    global _loop_duration, _loop_length_ticks
    stop()
    _loop_duration = None
    _loop_length_ticks = None
    for track in _tracks:
        track.events = []
        track.muted = False
        track.volume = 100


def clear_track(index: int) -> None:
    global _record_start
    if not 0 <= index < TRACK_COUNT:
        return
    track = _tracks[index]
    if track.state == "recording":
        _record_start = None  # the armed take is being discarded, not closed
    track.events = []
    track.overdub_events = []
    track.state = "stopped"
    _maybe_stop_playback()


def _has_content() -> bool:
    """Anything worth keeping the cycle alive for: recorded events anywhere,
    or a take still open (hits may land on the cycle at any moment)."""
    return any(t.events for t in _tracks) or any(t.state == "recording" for t in _tracks)


def _maybe_stop_playback() -> None:
    """Ends global playback when the last track with content is gone."""
    global _loop_duration, _loop_length_ticks
    if _has_content():
        return
    _cancel_task()
    _loop_duration = None
    _loop_length_ticks = None
    for track in _tracks:
        track.overdub_events = []
        track.state = "stopped"


def _cancel_task() -> None:
    global _task, _loop_start, _loop_start_tick
    if _task is not None:
        _task.cancel()
        _task = None
    _loop_start = None
    _loop_start_tick = None


def _quantize_to_bar(
    duration: float, settings: dict, minimum_duration: Optional[float] = None
) -> float:
    """Rounds duration up/down to the nearest whole bar at the current
    tempo/signature (minimum one bar), so the loop repeats in sync with the
    beat instead of drifting by whatever the raw hold time was. When a
    minimum_duration is supplied, expands to the next whole bar if nearest
    rounding would put a recorded event outside the cycle - scheduling an
    event after the cycle end makes the following cycle fire late. Falls
    back to the raw duration if bpm/settings are somehow unusable
    (defensive - should never actually happen, sequencer_bpm always has a
    default)."""
    try:
        bpm = float(settings.get("sequencer_bpm", 100))
    except (TypeError, ValueError):
        return duration
    if bpm <= 0:
        return duration
    signature = settings.get("metronome_signature", time_signatures.DEFAULT_SIGNATURE)
    pulses = time_signatures.get(signature)["pulses"]
    bar_duration = pulses * (60.0 / bpm)
    if bar_duration <= 0:
        return duration
    bars = max(1, round(duration / bar_duration))
    quantized = bars * bar_duration
    if minimum_duration is not None and quantized < minimum_duration:
        quantized = max(1, math.ceil(minimum_duration / bar_duration)) * bar_duration
    return quantized


def _apply_track_gain(track: Track, velocity: int) -> int:
    """Velocity after the track's volume scaling (percent), clamped to the
    MIDI range. 0 means "skip the hit" (muted, or volume at zero)."""
    if track.muted:
        return 0
    return max(0, min(127, round(velocity * track.volume / 100)))


def _merged_events() -> list[tuple[float, int, int, Track]]:
    """Flat (offset, pad_number, velocity, track) view of every sounding
    track's events, cycle-sorted. Tracks still recording are excluded -
    a take is silent until it closes. Snapshot per cycle, so an overdub
    merged mid-cycle only joins on the next one."""
    merged: list[tuple[float, int, int, Track]] = []
    for track in _tracks:
        if track.state not in _ACTIVE_STATES:
            continue
        for event in track.events:
            position = event.get("tick") if _loop_length_ticks is not None else event.get("offset")
            if position is not None:
                merged.append((position, event["pad_number"], event["velocity"], track))
    return sorted(merged, key=lambda item: item[0])


def _next_cycle_start(cycle_start: float, duration: float, now: float) -> float:
    """Returns the first cycle boundary strictly after now.

    A delayed asyncio task must skip expired cycles instead of immediately
    replaying their events, which produces MIDI bursts and worsens overload.
    """
    if now < cycle_start:
        return cycle_start
    elapsed_cycles = math.floor((now - cycle_start) / duration)
    return cycle_start + (elapsed_cycles + 1) * duration


def _next_cycle_tick(cycle_tick: int, length_ticks: int, now_tick: int) -> int:
    if now_tick < cycle_tick:
        return cycle_tick
    return cycle_tick + ((now_tick - cycle_tick) // length_ticks + 1) * length_ticks


async def _playback_loop(pads: list[dict], settings: dict) -> None:
    if _loop_length_ticks is not None:
        await _playback_tick_loop(pads, settings)
        return
    global _loop_start
    _loop_start = time.monotonic()
    try:
        while _loop_duration and _has_content():
            for offset, pad_number, velocity, track in _merged_events():
                delay = (_loop_start + offset) - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -0.02:
                    continue  # the event belongs to an expired cycle
                if not _loop_duration:
                    return
                fired = _apply_track_gain(track, velocity)  # mute/volume react mid-cycle
                if fired > 0:
                    await trigger.trigger_pad(pad_number, pads, fired, settings)
            remaining = (_loop_start + _loop_duration) - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            _loop_start = _next_cycle_start(_loop_start, _loop_duration, time.monotonic())
    except asyncio.CancelledError:
        pass


async def _playback_tick_loop(pads: list[dict], settings: dict) -> None:
    global _loop_start_tick
    if _loop_length_ticks is None:
        return
    _loop_start_tick = transport.shared().next_grid_tick(_loop_length_ticks)
    try:
        while _loop_length_ticks and _has_content():
            for offset, pad_number, velocity, track in _merged_events():
                target_tick = _loop_start_tick + int(offset)
                delay = transport.seconds_until_tick(target_tick)
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -0.02:
                    continue
                if not _loop_length_ticks:
                    return
                fired = _apply_track_gain(track, velocity)
                if fired > 0:
                    await trigger.trigger_pad(pad_number, pads, fired, settings)
            _loop_start_tick = _next_cycle_tick(
                _loop_start_tick, _loop_length_ticks, transport.tick_at()
            )
    except asyncio.CancelledError:
        pass

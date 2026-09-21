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
import time
from typing import Optional

from engine import time_signatures, trigger

TRACK_COUNT = 4

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
_started_at: Optional[float] = None
_task: Optional[asyncio.Task] = None
_playback_args: Optional[tuple[list[dict], dict]] = None  # pads/settings reused by record_start's auto-resume


def get_state() -> dict:
    return {
        "state": _global_state(),
        "selected_track": _selected,
        "loop_duration": _loop_duration,
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


def record_start() -> None:
    global _record_start, _started_at, _task
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
    _started_at = time.time()


def overdub_start() -> None:
    global _started_at
    track = _tracks[_selected]
    if track.state != "playing":
        return
    track.overdub_events = []
    track.state = "overdubbing"
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
    track = _tracks[_selected]
    if track.state == "recording":
        now = time.monotonic()
        if _loop_duration and _loop_start is not None:
            offset = (now - _loop_start) % _loop_duration
        elif _record_start is not None:
            offset = now - _record_start
        else:
            return
        track.events.append({"offset": offset, "pad_number": pad_number, "velocity": velocity})
    elif track.state == "overdubbing" and _loop_start is not None and _loop_duration:
        offset = (time.monotonic() - _loop_start) % _loop_duration
        track.overdub_events.append({"offset": offset, "pad_number": pad_number, "velocity": velocity})


async def record_stop(pads: list[dict], settings: dict) -> None:
    global _loop_duration, _record_start, _started_at, _task
    track = _tracks[_selected]
    if track.state != "recording" or _record_start is None:
        return
    held = time.monotonic() - _record_start
    _record_start = None
    if not track.events:
        track.state = "stopped"
        _maybe_stop_playback()
        return
    if _loop_duration is None:
        # First take to close: it fixes the shared cycle length.
        duration = held
        if settings.get("looper_quantize_enabled", "1") == "1":
            duration = _quantize_to_bar(duration, settings)
        _loop_duration = duration
    track.state = "playing"
    _started_at = time.time()
    _playback_args = (pads, settings)
    if _task is None or _task.done():
        _task = asyncio.create_task(_playback_loop(pads, settings))


def stop() -> None:
    """Stops the whole cycle (all tracks) but keeps every track's events -
    play_start resumes them; clear/clear_track are what erase content."""
    global _record_start
    _cancel_task()
    _record_start = None
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
    global _loop_duration
    stop()
    _loop_duration = None
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
    global _loop_duration
    if _has_content():
        return
    _cancel_task()
    _loop_duration = None
    for track in _tracks:
        track.overdub_events = []
        track.state = "stopped"


def _cancel_task() -> None:
    global _task, _loop_start
    if _task is not None:
        _task.cancel()
        _task = None
    _loop_start = None


def _quantize_to_bar(duration: float, settings: dict) -> float:
    """Rounds duration up/down to the nearest whole bar at the current
    tempo/signature (minimum one bar), so the loop repeats in sync with the
    beat instead of drifting by whatever the raw hold time was. Falls back
    to the raw duration if bpm/settings are somehow unusable (defensive -
    should never actually happen, sequencer_bpm always has a default)."""
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
    return bars * bar_duration


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
            merged.append((event["offset"], event["pad_number"], event["velocity"], track))
    return sorted(merged, key=lambda item: item[0])


async def _playback_loop(pads: list[dict], settings: dict) -> None:
    global _loop_start
    _loop_start = time.monotonic()
    try:
        while _loop_duration and _has_content():
            for offset, pad_number, velocity, track in _merged_events():
                delay = (_loop_start + offset) - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                if not _loop_duration:
                    return
                fired = _apply_track_gain(track, velocity)  # mute/volume react mid-cycle
                if fired > 0:
                    await trigger.trigger_pad(pad_number, pads, fired, settings)
            remaining = (_loop_start + _loop_duration) - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            _loop_start += _loop_duration
    except asyncio.CancelledError:
        pass

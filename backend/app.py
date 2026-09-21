"""DiakoPad web app: pad grid + sound library, served to the touchscreen and
to any browser on the LAN. Owns its own audio engine directly - one sfizz
instance per pad, per-pad configurable mod-host effect chains, a step
sequencer, a live looper and a metronome - orchestrated by
engine/orchestrator.py."""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import kit_import
import midi
import storage
from engine import effects_catalog, knob_registry, looper, metronome, metronome_sounds, orchestrator, sequencer, tempo, time_signatures, trigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diakopad")

BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
SAMPLES_DIR = BASE_DIR / "samples"
SAMPLES_DIR.mkdir(exist_ok=True)

SLOT_PARAM_RE = re.compile(r"^slot(\d+):(.+)$")

app = FastAPI(title="DiakoPad")


class AssignRequest(BaseModel):
    sample_id: Optional[int] = None


class NoteRequest(BaseModel):
    midi_note: int


class MixRequest(BaseModel):
    volume_db: Optional[float] = None
    pan: Optional[float] = None
    tone: Optional[float] = None  # 0-100; 100 = filter bypassed. None = leave unchanged.


class LearnRequest(BaseModel):
    scope: str  # "pad" | "global"
    pad_number: Optional[int] = None
    param: str


class SettingsRequest(BaseModel):
    sustain_mode: Optional[bool] = None
    velocity_sensitive: Optional[bool] = None


class EffectSlotRequest(BaseModel):
    plugin_id: Optional[str] = None


class EffectParamRequest(BaseModel):
    symbol: str
    value: float


class SequencerTransportRequest(BaseModel):
    running: bool


class SequencerStepRequest(BaseModel):
    active: bool


class TempoRequest(BaseModel):
    bpm: float


class MetronomeTransportRequest(BaseModel):
    running: bool


class MetronomeStyleRequest(BaseModel):
    style: str


class MetronomeSignatureRequest(BaseModel):
    signature: str


class MasterRequest(BaseModel):
    volume: Optional[float] = None
    muted: Optional[bool] = None
    limiter_enabled: Optional[bool] = None
    limiter_threshold_db: Optional[float] = None


class SceneRequest(BaseModel):
    name: str


class PatternRequest(BaseModel):
    name: str


class KitBrowseNavRequest(BaseModel):
    direction: str


class KitBrowseSelectTargetRequest(BaseModel):
    pad_number: int


class KitBrowsePreviewRequest(BaseModel):
    enabled: bool


class LooperQuantizeRequest(BaseModel):
    enabled: bool


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        stale = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()
_sequencer_tick_task: Optional[asyncio.Task] = None
_pending_sequencer_step: Optional[int] = None
_metronome_tick_task: Optional[asyncio.Task] = None
_pending_metronome_beat: Optional[int] = None
_metronome_style_lock = asyncio.Lock()
_engine_status_task: Optional[asyncio.Task] = None
_pad_notes: dict[int, list[int]] = {}
_pending_pad_hits: dict[int, int] = {}
_pad_hit_task: Optional[asyncio.Task] = None
_pending_note_learn: Optional[int] = None  # pad_number waiting for a physical hit, or None

# --- Kit browse mode ---------------------------------------------------------
# Lets a physical pad's sound be reassigned from the `kits` catalog (curated
# sound sets, distinct from scenes - see storage.py) without ever touching
# the touchscreen. Two-step, per-pad flow, not a global "browse everything"
# mode:
#   IDLE   -[press the learned toggle]->  ARMED (waiting for a pad tap)
#   ARMED  -[tap any pad, 1-16]->         BROWSING, that pad is the target
#   ARMED  -[press the toggle again]->    IDLE (cancel, nothing happened)
# Once BROWSING, on the 4x4 grid (top row = 13,14,15,16; ...; bottom row =
# 1,2,3,4 - see frontend/app.js's displayOrder()):
#   13 Up (prev kit)    14 Left (prev sound)  15 Right (next sound)  16 Down (next kit)
#   1 Back (cancel, target pad keeps its old sound)
#   4 Confirm (apply the candidate sound to the target pad only, remember
#              this kit, exit)
#   everything else: jumps straight to THAT pad-role's sound in the
#              highlighted kit as the new candidate (previewed if the
#              setting allows - see _kit_browse_maybe_preview) - a shortcut
#              alongside stepping one at a time with left/right.
# Kits are one flat list (storage.list_kits(), already ordered by category/
# sort_index/name) - "category" is just metadata on a kit, not a separate
# navigation level: each imported folder (e.g. "Akai MPC-60") is its own
# unit, stepped through directly with up/down; left/right step through that
# kit's own sounds instead.
# None = IDLE (pads behave normally). See _handle_kit_browse_note,
# _kit_browse_arm/_select_target/_nav/_select_candidate/_confirm/_back below.
_kit_browse_state: Optional[dict] = None
_KIT_BROWSE_NAV_PADS = {13: "up", 14: "left", 15: "right", 16: "down"}
_KIT_BROWSE_BACK_PAD = 1
_KIT_BROWSE_CONFIRM_PAD = 4

# --- Controller action bindings ---------------------------------------------
# Dedicated SMC-PAD controls with no on-screen equivalent (the "Gravar"
# button, the side arrow, the "bak" pad, a panic button) get bound here to a
# fixed logical action instead of a pad or a knob param. Checked before
# pad-note-learn/pad-trigger in _handle_note and before knob-learn/CC-target
# in _handle_cc, so a dedicated control never accidentally plays a pad or
# tweaks a knob.
CONTROLLER_ACTIONS = ("scene_next", "scene_prev", "looper_record_toggle", "looper_play_toggle", "looper_overdub_toggle", "panic", "tap_tempo", "kit_browse_toggle", "kit_browse_up", "kit_browse_down", "kit_browse_left", "kit_browse_right", "kit_browse_confirm", "kit_browse_back")
_pending_controller_learn: Optional[str] = None  # action waiting for a physical signal, or None
_scene_switch_lock = asyncio.Lock()
# Last CC value seen per CC-bound controller action. Buttons fire on the
# rising edge (0 -> nonzero) only: a momentary button sends a burst of
# nonzero values while held and 0 on release, and each message must NOT
# re-trigger the action or a single press would switch scenes repeatedly.
_cc_action_last_values: dict[int, int] = {}

# --- Knob MIDI-learn state -------------------------------------------------
# The MIDI input callback fires on mido/rtmidi's own thread; everything here
# only ever runs on the main asyncio loop, reached via call_soon_threadsafe.
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_learn: Optional[dict] = None  # {"scope": str, "pad_number": int|None, "param": str}
_pending_cc_values: dict[tuple[str, Optional[int], str], int] = {}
_debounce_handles: dict[tuple[str, Optional[int], str], asyncio.TimerHandle] = {}
KNOB_DEBOUNCE_SECONDS = 0.3

# Some physical buttons (observed on the SMC-PAD) send TWO genuine Note On
# messages, both with real velocity, for a single physical press - not the
# already-handled velocity<=0 release encoding, just a hardware/firmware
# double-fire a few ms apart. For a note bound to a toggle-style controller
# action (kit_browse_toggle, panic, ...) that turns one press into an
# immediate on/off no-op, so the SAME note re-dispatching within this window
# is swallowed. Far below any realistic human tap_tempo interval.
_bound_note_last_dispatch: dict[int, float] = {}
BOUND_NOTE_DEBOUNCE_SECONDS = 0.15


def _frac_to_cutoff(frac: float) -> Optional[float]:
    """0..1 -> 200Hz..20kHz log scale. >=0.99 means "fully open" (no filter)."""
    if frac >= 0.99:
        return None
    return round(200 * (20000 / 200) ** frac)


def _parse_slot_param(param: str) -> Optional[tuple[int, str]]:
    m = SLOT_PARAM_RE.match(param)
    return (int(m.group(1)), m.group(2)) if m else None


def _on_midi_cc(control: int, value: int) -> None:
    if _main_loop is not None:
        _main_loop.call_soon_threadsafe(_handle_cc, control, value)


def _on_midi_note(note: int, velocity: int) -> None:
    if _main_loop is not None:
        _main_loop.call_soon_threadsafe(_handle_note, note, velocity)


def _handle_note(note: int, velocity: int) -> None:
    """Feeds the live looper's recorder and per-pad MIDI-note learn. Only
    ever sees genuine hardware hits - sequencer/looper-triggered notes go
    out DiakoPad-trigger-out straight into each pad's sfizz instance, never
    back through this input port.

    A velocity of 0 is the common "note off" encoding and must never trigger
    a pad, a knob-learn or a controller action (e.g. tap tempo) - some
    controllers send the release that way instead of a real Note Off."""
    global _pending_note_learn, _pending_controller_learn
    if velocity <= 0:
        return
    asyncio.create_task(_broadcast_midi_note(note, velocity))

    if _pending_controller_learn is not None:
        action = _pending_controller_learn
        _pending_controller_learn = None
        asyncio.create_task(_apply_controller_learn(action, "note", note))
        return

    bound_action = storage.get_action_for_signal("note", note)
    if bound_action is not None:
        now = time.monotonic()
        last = _bound_note_last_dispatch.get(note, 0.0)
        _bound_note_last_dispatch[note] = now
        if now - last < BOUND_NOTE_DEBOUNCE_SECONDS:
            return
        asyncio.create_task(_dispatch_controller_action(bound_action))
        return

    if _kit_browse_state is not None:
        asyncio.create_task(_handle_kit_browse_note(note))
        return

    if _pending_note_learn is not None:
        pad_number = _pending_note_learn
        _pending_note_learn = None
        asyncio.create_task(_apply_note_learn(pad_number, note))
        return

    pad_numbers = _pad_notes.get(note, [])
    if not pad_numbers:
        return
    for pad_number in pad_numbers:
        _queue_pad_hit(pad_number, velocity)
        if looper.get_state()["state"] in ("recording", "overdubbing"):
            looper.record_event(pad_number, velocity)


async def _apply_note_learn(pad_number: int, note: int) -> None:
    global _pad_notes
    storage.set_pad_note(pad_number, note)
    _pad_notes = _build_pad_note_map(storage.list_pads())
    await orchestrator.apply_pad(
        pad_number, storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    await _broadcast_pads()
    await _broadcast_note_learn()


async def _broadcast_note_learn() -> None:
    await manager.broadcast({"type": "note_learn", "pending_pad": _pending_note_learn})


async def _apply_controller_learn(action: str, midi_type: str, number: int) -> None:
    storage.add_controller_binding(action, midi_type, number)
    await _broadcast_controller_actions()


async def _broadcast_controller_actions() -> None:
    await manager.broadcast(
        {
            "type": "controller_actions",
            "bindings": storage.list_controller_bindings(),
            "pending_learn": _pending_controller_learn,
        }
    )


# --- Kit browse mode ---------------------------------------------------------


async def _handle_kit_browse_note(note: int) -> None:
    """Interprets a pad hit while kit-browse mode is active (_kit_browse_state
    is not None), per the fixed layout documented next to _kit_browse_state
    above. Reuses each pad's already-configured live MIDI note (via
    _pad_notes) - no separate learn step - so the same physical taps work
    whether or not the pad currently has a sound assigned."""
    pad_numbers = _pad_notes.get(note, [])
    if not pad_numbers:
        return
    pad_number = pad_numbers[0]

    if _kit_browse_state.get("target_pad") is None:
        await _kit_browse_select_target(pad_number)
        return

    if pad_number == _KIT_BROWSE_BACK_PAD:
        await _kit_browse_back()
    elif pad_number == _KIT_BROWSE_CONFIRM_PAD:
        await _kit_browse_confirm()
    elif pad_number in _KIT_BROWSE_NAV_PADS:
        await _kit_browse_nav(_KIT_BROWSE_NAV_PADS[pad_number])
    else:
        await _kit_browse_select_candidate(pad_number)


def _kit_browse_default_candidate(kit: dict, target_pad: int) -> Optional[int]:
    """A kit's own pad_number numbering has no relation to which physical pad
    is being edited - it's just the order curate_kit_pack.py happened to find
    sounds in. Default to "the kit's sound at the same number as the target
    pad" when it has one (the common case: filling pads in the kit's own
    order), else the kit's first populated sound, else None (empty kit)."""
    pads = kit.get("pads") or []
    if any(p["pad_number"] == target_pad for p in pads):
        return target_pad
    return pads[0]["pad_number"] if pads else None


async def _kit_browse_maybe_preview(kit: dict, candidate_pad_number: Optional[int]) -> None:
    """Plays the candidate sound through the isolated preview engine, but
    only when the persistent "preview ao navegar kits" setting is on AND the
    kit actually has a sound at that number - stays silent (but still updates
    state) otherwise, e.g. mid-performance with preview turned off."""
    if candidate_pad_number is None:
        return
    if storage.get_settings().get("kit_browse_preview_enabled") != "1":
        return
    entry = next((p for p in kit.get("pads", []) if p["pad_number"] == candidate_pad_number), None)
    if entry is None or entry.get("sample_id") is None:
        return
    await trigger.trigger_preview(candidate_pad_number)


def _kit_browse_payload() -> dict:
    if _kit_browse_state is None:
        return {"type": "kit_browse", "active": False}
    state = _kit_browse_state
    target_pad = state.get("target_pad")
    if target_pad is None:
        return {"type": "kit_browse", "active": True, "phase": "armed", "target_pad": None}
    kit = state["kits"][state["kit_idx"]]
    candidate_pad_number = state.get("candidate_pad_number")
    sounds = sorted(
        (p for p in kit.get("pads", []) if p.get("sample_id") is not None),
        key=lambda p: p["pad_number"],
    )
    candidate_entry = next((p for p in sounds if p["pad_number"] == candidate_pad_number), None)
    sound_index = next(
        (i for i, p in enumerate(sounds) if p["pad_number"] == candidate_pad_number), None
    )
    return {
        "type": "kit_browse",
        "active": True,
        "phase": "browsing",
        "target_pad": target_pad,
        # The full flat kit list (not just the current one) so the UI can
        # show where you are among every kit, not just a bare "2/16"
        # counter. No separate category level - each imported folder is its
        # own unit, "category" is just metadata on it.
        "kits": [{"id": k["id"], "name": k["name"]} for k in state["kits"]],
        "kit": {"id": kit["id"], "name": kit["name"]},
        "kit_index": state["kit_idx"],
        "kit_count": len(state["kits"]),
        # Every sound the highlighted kit actually has (pads with no sample
        # assigned are left out), so the UI can list them all with the
        # current candidate picked out - not just the one candidate name.
        "sounds": [
            {"pad_number": p["pad_number"], "display_name": p["display_name"]} for p in sounds
        ],
        "candidate_pad_number": candidate_pad_number,
        "candidate_display_name": candidate_entry["display_name"] if candidate_entry else None,
        "sound_index": sound_index,
        "sound_count": len(sounds),
    }


async def _broadcast_kit_browse() -> None:
    await manager.broadcast(_kit_browse_payload())


async def _kit_browse_arm() -> None:
    """First step of the flow: wait for a tap on the pad the user wants to
    reassign. No engine work yet - there's no kit in destaque without a
    target pad to compute a default candidate against."""
    global _kit_browse_state
    _kit_browse_state = {"target_pad": None}
    await _broadcast_kit_browse()


async def _kit_browse_select_target(pad_number: int) -> None:
    """Second step: pad_number becomes the pad being edited. Starts browsing
    at the last kit a confirm actually used (kit_browse_last_kit_id), since
    the tendency is to fill several pads from the same kit in a row, falling
    back to the first kit overall the first time ever / if that kit was
    deleted since. A no-op back to IDLE when the catalog is empty - nothing
    to browse yet."""
    global _kit_browse_state
    kits = storage.list_kits()
    if not kits:
        _kit_browse_state = None
        await _broadcast_kit_browse()
        return

    last_kit_id = storage.get_settings().get("kit_browse_last_kit_id") or ""
    kit_idx = 0
    if last_kit_id:
        kit_idx = next((i for i, k in enumerate(kits) if str(k["id"]) == last_kit_id), 0)

    kit = kits[kit_idx]
    _kit_browse_state = {
        "target_pad": pad_number,
        "kits": kits,
        "kit_idx": kit_idx,
        "candidate_pad_number": _kit_browse_default_candidate(kit, pad_number),
    }
    await orchestrator.apply_preview_kit(kit)
    await _kit_browse_maybe_preview(kit, _kit_browse_state["candidate_pad_number"])
    await _broadcast_kit_browse()


async def _kit_browse_nav(direction: str) -> None:
    """up/down step through the flat kit list, one folder at a time (no
    separate category level - see the module comment above _kit_browse_state)
    and land on that kit's default candidate. left/right instead step
    through the CURRENT kit's own sounds one at a time, in pad_number order
    - a quicker alternative to always tapping that sound's specific pad. All
    four wrap around."""
    state = _kit_browse_state
    if state is None or state.get("target_pad") is None:
        return

    kit_changed = False
    if direction in ("up", "down"):
        delta = 1 if direction == "down" else -1
        state["kit_idx"] = (state["kit_idx"] + delta) % len(state["kits"])
        kit_changed = True

    kit = state["kits"][state["kit_idx"]]
    if kit_changed:
        state["candidate_pad_number"] = _kit_browse_default_candidate(kit, state["target_pad"])
        await orchestrator.apply_preview_kit(kit)
    else:
        sounds = sorted(
            (p for p in kit.get("pads", []) if p.get("sample_id") is not None),
            key=lambda p: p["pad_number"],
        )
        if sounds:
            numbers = [p["pad_number"] for p in sounds]
            current = state.get("candidate_pad_number")
            idx = numbers.index(current) if current in numbers else 0
            delta = 1 if direction == "right" else -1
            state["candidate_pad_number"] = numbers[(idx + delta) % len(numbers)]

    await _kit_browse_maybe_preview(kit, state["candidate_pad_number"])
    await _broadcast_kit_browse()


async def _kit_browse_select_candidate(pad_number: int) -> None:
    """Tapping one of the "free" pads while browsing overrides the default
    candidate with that specific sound from the highlighted kit - lets you
    pick any of the kit's sounds for the target pad, not just the one at the
    same number. Doesn't change kit or leave browsing."""
    state = _kit_browse_state
    if state is None or state.get("target_pad") is None:
        return
    state["candidate_pad_number"] = pad_number
    kit = state["kits"][state["kit_idx"]]
    await _kit_browse_maybe_preview(kit, pad_number)
    await _broadcast_kit_browse()


async def _kit_browse_confirm() -> None:
    """Applies the current candidate sound onto the target pad ONLY - every
    other live pad is untouched, unlike the old whole-kit apply. A candidate
    number the kit has no sound at (or no candidate at all, e.g. an empty
    kit) clears the target pad instead, which also doubles as a deliberate
    way to empty a pad by browsing to a gap. Remembers this kit for next
    time (see _kit_browse_select_target) before leaving."""
    global _kit_browse_state
    state = _kit_browse_state
    if state is None or state.get("target_pad") is None:
        return
    target_pad = state["target_pad"]
    kit = state["kits"][state["kit_idx"]]
    entry = next((p for p in kit.get("pads", []) if p["pad_number"] == state.get("candidate_pad_number")), None)
    sample_id = entry["sample_id"] if entry is not None else None

    storage.assign_sample(target_pad, sample_id)
    settings = storage.get_settings()
    pad_effects = storage.list_pad_effects()
    pads_after = storage.list_pads()
    await orchestrator.apply_pad(target_pad, pads_after, settings, pad_effects)
    storage.set_setting("kit_browse_last_kit_id", str(kit["id"]))

    await orchestrator.stop_preview()
    _kit_browse_state = None
    await _broadcast_pads()
    await _broadcast_kit_browse()


async def _kit_browse_back() -> None:
    """Cancels out of ARMED or BROWSING with no changes to any live pad -
    only the preview instance (if it was ever started) is touched."""
    global _kit_browse_state
    await orchestrator.stop_preview()
    _kit_browse_state = None
    await _broadcast_kit_browse()


async def _dispatch_controller_action(action: str) -> None:
    if action == "panic":
        await orchestrator.panic(storage.list_pads())
        await _broadcast_master()
        await _broadcast_looper()
        await _broadcast_sequencer()
        await _broadcast_metronome()
    elif action == "tap_tempo":
        bpm = tempo.register_tap()
        if bpm is not None:
            tempo.set(bpm)
            await _broadcast_tempo()
    elif action == "looper_record_toggle":
        if looper.get_state()["state"] == "recording":
            await looper.record_stop(storage.list_pads(), storage.get_settings())
        else:
            looper.record_start()
        await _broadcast_looper()
        await manager.broadcast({"type": "navigate", "view": "looper"})
    elif action == "looper_play_toggle":
        if looper.get_state()["state"] in ("playing", "overdubbing"):
            looper.stop()
        else:
            await looper.play_start(storage.list_pads(), storage.get_settings())
        await _broadcast_looper()
        await manager.broadcast({"type": "navigate", "view": "looper"})
    elif action == "looper_overdub_toggle":
        if looper.get_state()["state"] == "overdubbing":
            looper.overdub_stop()
        else:
            looper.overdub_start()
        await _broadcast_looper()
        await manager.broadcast({"type": "navigate", "view": "looper"})
    elif action in ("scene_next", "scene_prev"):
        async with _scene_switch_lock:
            scenes = storage.list_active_scenes()
            if not scenes:
                return
            current_id = storage.get_settings().get("current_scene_id") or ""
            idx = next((i for i, s in enumerate(scenes) if str(s["id"]) == current_id), -1)
            if idx == -1:
                # No current scene: land on the first one going forward, the
                # last one going backward.
                idx = len(scenes) if action == "scene_prev" else 0
            next_scene_id = scenes[(idx + 1) % len(scenes)]["id"] if action == "scene_next" else scenes[(idx - 1) % len(scenes)]["id"]
            scene = storage.load_scene(next_scene_id)
            storage.set_setting("current_scene_id", str(next_scene_id))
            await _apply_scene_and_broadcast(scene)
            await _broadcast_scenes()
    elif action == "kit_browse_toggle":
        if _kit_browse_state is None:
            await _kit_browse_arm()
        else:
            await _kit_browse_back()
    elif action in ("kit_browse_up", "kit_browse_down", "kit_browse_left", "kit_browse_right"):
        # Optional alternative to the fixed pad-corner layout: bind a
        # dedicated physical control (e.g. the SMC-PAD's side arrow) to one
        # of these instead of repurposing a pad. A no-op while not actively
        # browsing (_kit_browse_nav's own guard).
        await _kit_browse_nav(action.removeprefix("kit_browse_"))
    elif action == "kit_browse_confirm":
        await _kit_browse_confirm()
    elif action == "kit_browse_back":
        await _kit_browse_back()


def _handle_cc(control: int, value: int) -> None:
    global _pending_learn, _pending_controller_learn
    if _pending_controller_learn is not None:
        action = _pending_controller_learn
        _pending_controller_learn = None
        asyncio.create_task(_apply_controller_learn(action, "cc", control))
        return

    bound_action = storage.get_action_for_signal("cc", control)
    if bound_action is not None:
        last_value = _cc_action_last_values.get(control, 0)
        _cc_action_last_values[control] = value
        if value > 0 and last_value == 0:
            asyncio.create_task(_dispatch_controller_action(bound_action))
        return

    if _pending_learn is not None:
        storage.set_knob_mapping(
            control, _pending_learn["scope"], _pending_learn["pad_number"], _pending_learn["param"]
        )
        _pending_learn = None
        asyncio.create_task(_broadcast_knobs())
        return

    target = storage.get_knob_target(control)
    if target is None:
        return
    key = (target["scope"], target["pad_number"], target["param"])
    _pending_cc_values[key] = value
    old_handle = _debounce_handles.get(key)
    if old_handle is not None:
        old_handle.cancel()
    _debounce_handles[key] = _main_loop.call_later(
        KNOB_DEBOUNCE_SECONDS, lambda: asyncio.create_task(_apply_knob_target(key))
    )


async def _apply_knob_target(key: tuple[str, Optional[int], str]) -> None:
    _debounce_handles.pop(key, None)
    cc_value = _pending_cc_values.pop(key, None)
    if cc_value is None:
        return
    scope, pad_number, param = key
    pad_effects = storage.list_pad_effects()
    meta = knob_registry.resolve(scope, pad_number, param, pad_effects)
    if meta is None:
        return  # stale mapping - e.g. the effect slot it pointed to was emptied since
    value = knob_registry.cc_to_value(cc_value, meta)

    if scope == "global" and param == "tempo":
        tempo.set(value)
        await _broadcast_tempo()
        return

    slot = _parse_slot_param(param)
    if slot is not None:
        slot_index, symbol = slot
        storage.set_pad_effect_param(pad_number, slot_index, symbol, value)
        await orchestrator.set_effect_param(pad_number, slot_index, symbol, value)
        await _broadcast_pad_effects()
        return

    if param == "volume":
        storage.set_pad_mix(pad_number, volume_db=value)
    elif param == "pan":
        storage.set_pad_mix(pad_number, pan=value)
    elif param == "tone":
        storage.set_pad_mix(pad_number, cutoff_hz=value)
    await orchestrator.apply_pad(pad_number, storage.list_pads(), storage.get_settings(), pad_effects)
    await _broadcast_pads()


# --- Startup -----------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    global _main_loop, _pad_notes
    storage.init_db()
    _main_loop = asyncio.get_event_loop()
    midi.open_input(_on_midi_cc, _on_midi_note)
    await orchestrator.startup()
    settings = storage.get_settings()
    _pad_notes = _build_pad_note_map(storage.list_pads())
    await orchestrator.apply_master(
        float(settings.get("master_volume", 100)),
        settings.get("master_muted") == "1",
        settings.get("master_limiter_enabled", "1") == "1",
        float(settings.get("master_limiter_threshold_db", -1)),
    )
    await orchestrator.apply_all_pads(storage.list_pads(), settings, storage.list_pad_effects())
    sequencer.load_pattern(storage.list_sequencer_steps())
    tempo.load()
    metronome.set_signature(settings.get("metronome_signature", time_signatures.DEFAULT_SIGNATURE))
    await orchestrator.apply_metronome_style(settings.get("metronome_style", metronome_sounds.DEFAULT_STYLE))
    global _engine_status_task
    _engine_status_task = asyncio.create_task(_broadcast_engine_status_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await sequencer.stop()
    await metronome.stop()
    looper.stop()
    global _engine_status_task
    if _engine_status_task is not None:
        _engine_status_task.cancel()
        _engine_status_task = None
    await orchestrator.shutdown()


def _pads_payload() -> list[dict]:
    pads = storage.list_pads()
    for p in pads:
        p["has_sample"] = p["sample_id"] is not None
    return pads


async def _broadcast_pads() -> None:
    await manager.broadcast({"type": "pads", "pads": _pads_payload()})


async def _broadcast_pad_hit(pad_number: int, velocity: int) -> None:
    await manager.broadcast({"type": "pad_hit", "pad_number": pad_number, "velocity": velocity})


async def _broadcast_midi_note(note: int, velocity: int) -> None:
    await manager.broadcast({"type": "midi_note", "note": note, "velocity": velocity})


def _build_pad_note_map(pads: list[dict]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for pad in pads:
        result.setdefault(pad["midi_note"], []).append(pad["pad_number"])
    return result


def _queue_pad_hit(pad_number: int, velocity: int) -> None:
    global _pad_hit_task
    _pending_pad_hits[pad_number] = velocity
    if _pad_hit_task is None or _pad_hit_task.done():
        _pad_hit_task = asyncio.create_task(_drain_pad_hits())


async def _drain_pad_hits() -> None:
    while _pending_pad_hits:
        hits = list(_pending_pad_hits.items())
        _pending_pad_hits.clear()
        for pad_number, velocity in hits:
            await _broadcast_pad_hit(pad_number, velocity)


async def _broadcast_sounds() -> None:
    await manager.broadcast({"type": "sounds", "sounds": storage.list_samples()})


def _enriched_knob_mappings() -> list[dict]:
    pad_effects = storage.list_pad_effects()
    enriched = []
    for m in storage.list_knob_mappings():
        meta = knob_registry.resolve(m["scope"], m["pad_number"], m["param"], pad_effects)
        param_label = meta["label"] if meta else m["param"]
        target_label = "Global" if m["scope"] == "global" else f"Pad {m['pad_number']}"
        enriched.append({**m, "label": f"{target_label} · {param_label}"})
    return enriched


async def _broadcast_knobs() -> None:
    await manager.broadcast(
        {"type": "knobs", "knobs": _enriched_knob_mappings(), "pending_learn": _pending_learn}
    )


async def _broadcast_pad_effects() -> None:
    await manager.broadcast({"type": "pad_effects", "pad_effects": storage.list_pad_effects()})


async def _on_sequencer_tick(current_step: int) -> None:
    global _sequencer_tick_task, _pending_sequencer_step
    _pending_sequencer_step = current_step
    if _sequencer_tick_task is None or _sequencer_tick_task.done():
        _sequencer_tick_task = asyncio.create_task(_drain_sequencer_ticks())


async def _drain_sequencer_ticks() -> None:
    global _pending_sequencer_step
    while _pending_sequencer_step is not None:
        current_step = _pending_sequencer_step
        _pending_sequencer_step = None
        await manager.broadcast({"type": "sequencer_tick", "current_step": current_step})


async def _broadcast_sequencer() -> None:
    await manager.broadcast({"type": "sequencer", **sequencer.get_state()})


async def _broadcast_patterns() -> None:
    await manager.broadcast({"type": "patterns", "patterns": storage.list_patterns()})


async def _broadcast_looper() -> None:
    await manager.broadcast({"type": "looper", **looper.get_state()})


async def _broadcast_tempo() -> None:
    await manager.broadcast({"type": "tempo", "bpm": tempo.get()})


async def _on_metronome_beat(beat_in_bar: int) -> None:
    global _metronome_tick_task, _pending_metronome_beat
    _pending_metronome_beat = beat_in_bar
    if _metronome_tick_task is None or _metronome_tick_task.done():
        _metronome_tick_task = asyncio.create_task(_drain_metronome_ticks())


async def _drain_metronome_ticks() -> None:
    global _pending_metronome_beat
    while _pending_metronome_beat is not None:
        beat_in_bar = _pending_metronome_beat
        _pending_metronome_beat = None
        await manager.broadcast({"type": "metronome_tick", "beat_in_bar": beat_in_bar})


async def _broadcast_metronome() -> None:
    await manager.broadcast(
        {
            "type": "metronome",
            **metronome.get_state(),
            "style": storage.get_settings().get("metronome_style", metronome_sounds.DEFAULT_STYLE),
        }
    )


async def _broadcast_master() -> None:
    await manager.broadcast({"type": "master", **orchestrator.master_state()})


async def _broadcast_engine_status() -> None:
    status = await asyncio.to_thread(orchestrator.engine_status)
    await manager.broadcast({"type": "engine_status", **status})


async def _broadcast_engine_status_loop() -> None:
    while True:
        await _broadcast_engine_status()
        await asyncio.sleep(3)


def _settings_payload() -> dict:
    # midi_channel is read-only here (set via DIAKOPAD_MIDI_CHANNEL at
    # deploy time, see backend/midi.py) - shown so the frontend doesn't need
    # to hardcode it.
    return {**storage.get_settings(), "midi_channel": midi.MIDI_CHANNEL + 1}


async def _broadcast_settings() -> None:
    await manager.broadcast({"type": "settings", "settings": _settings_payload()})


@app.get("/api/pads")
def get_pads():
    return _pads_payload()


@app.post("/api/pads/{pad_number}/assign")
async def assign_pad(pad_number: int, body: AssignRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if body.sample_id is not None:
        samples = {s["id"] for s in storage.list_samples()}
        if body.sample_id not in samples:
            raise HTTPException(404, "sample not found")

    storage.assign_sample(pad_number, body.sample_id)
    applied = await orchestrator.apply_pad(
        pad_number, storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )

    await _broadcast_pads()
    return {"ok": True, "engine_applied": applied}


@app.post("/api/pads/clear")
async def clear_all_pads():
    """Removes the sound from all 16 pads at once. Mix, effects and knobs are
    kept; only the sample assignment is cleared."""
    storage.clear_all_pads()
    await orchestrator.apply_all_pads(
        storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    await _broadcast_pads()
    return {"ok": True}


@app.post("/api/pads/{pad_number}/note")
async def set_pad_note(pad_number: int, body: NoteRequest):
    global _pad_notes
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 0 <= body.midi_note <= 127:
        raise HTTPException(400, "midi_note must be between 0 and 127")
    storage.set_pad_note(pad_number, body.midi_note)
    _pad_notes = _build_pad_note_map(storage.list_pads())
    await orchestrator.apply_pad(
        pad_number, storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    await _broadcast_pads()
    return {"ok": True}


@app.post("/api/pads/{pad_number}/note/learn")
async def start_note_learn(pad_number: int):
    global _pending_note_learn
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    _pending_note_learn = pad_number
    await _broadcast_note_learn()
    return {"ok": True}


@app.post("/api/pads/{pad_number}/note/learn/cancel")
async def cancel_note_learn(pad_number: int):
    global _pending_note_learn
    if _pending_note_learn == pad_number:
        _pending_note_learn = None
        await _broadcast_note_learn()
    return {"ok": True}


@app.post("/api/pads/{pad_number}/trigger")
async def trigger_pad(pad_number: int):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    pads = storage.list_pads()
    sent = await trigger.trigger_pad(pad_number, pads, 100, storage.get_settings())
    if sent:
        _queue_pad_hit(pad_number, 100)
        if looper.get_state()["state"] in ("recording", "overdubbing"):
            looper.record_event(pad_number, 100)
    return {"ok": sent}


@app.post("/api/pads/{pad_number}/mix")
async def set_pad_mix(pad_number: int, body: MixRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    cutoff_hz = "__unset__"
    if body.tone is not None:
        if not 0 <= body.tone <= 100:
            raise HTTPException(400, "tone must be between 0 and 100")
        cutoff_hz = _frac_to_cutoff(body.tone / 100)

    storage.set_pad_mix(pad_number, volume_db=body.volume_db, pan=body.pan, cutoff_hz=cutoff_hz)
    if body.volume_db is not None or body.pan is not None or body.tone is not None:
        await orchestrator.apply_pad(
            pad_number, storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
        )
    await _broadcast_pads()
    return {"ok": True}


@app.get("/api/knobs")
def get_knobs():
    return {"knobs": _enriched_knob_mappings(), "pending_learn": _pending_learn}


@app.get("/api/knobs/targets")
def get_knob_targets():
    pad_effects = storage.list_pad_effects()
    return {
        "global": knob_registry.GLOBAL_PARAMS,
        "pads": {str(n): knob_registry.list_params("pad", n, pad_effects) for n in range(1, 17)},
    }


@app.post("/api/knobs/learn")
async def start_knob_learn(body: LearnRequest):
    global _pending_learn
    if body.scope not in ("pad", "global"):
        raise HTTPException(400, "scope must be 'pad' or 'global'")
    if body.scope == "pad" and not (body.pad_number is not None and 1 <= body.pad_number <= 16):
        raise HTTPException(400, "pad_number must be between 1 and 16 for scope='pad'")
    pad_number = body.pad_number if body.scope == "pad" else None
    if knob_registry.resolve(body.scope, pad_number, body.param, storage.list_pad_effects()) is None:
        raise HTTPException(400, "unknown param for this target")
    _pending_learn = {"scope": body.scope, "pad_number": pad_number, "param": body.param}
    await _broadcast_knobs()
    return {"ok": True}


@app.post("/api/knobs/learn/cancel")
async def cancel_knob_learn():
    global _pending_learn
    _pending_learn = None
    await _broadcast_knobs()
    return {"ok": True}


@app.delete("/api/knobs/{cc_number}")
async def remove_knob_mapping(cc_number: int):
    storage.delete_knob_mapping(cc_number)
    await _broadcast_knobs()
    return {"ok": True}


@app.delete("/api/knobs")
async def clear_knob_mappings():
    global _pending_learn
    _pending_learn = None
    storage.clear_knob_mappings()
    await _broadcast_knobs()
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return _settings_payload()


@app.post("/api/settings")
async def update_settings(body: SettingsRequest):
    if body.sustain_mode is not None:
        storage.set_setting("sustain_mode", "1" if body.sustain_mode else "0")
    if body.velocity_sensitive is not None:
        storage.set_setting("velocity_sensitive", "1" if body.velocity_sensitive else "0")
    await orchestrator.apply_all_pads(
        storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    await _broadcast_settings()
    return {"ok": True}


@app.get("/api/master")
def get_master():
    return orchestrator.master_state()


@app.post("/api/master")
async def set_master(body: MasterRequest):
    settings = storage.get_settings()
    volume = float(settings.get("master_volume", 100)) if body.volume is None else body.volume
    muted = settings.get("master_muted") == "1" if body.muted is None else body.muted
    limiter_enabled = settings.get("master_limiter_enabled", "1") == "1" if body.limiter_enabled is None else body.limiter_enabled
    limiter_threshold_db = float(settings.get("master_limiter_threshold_db", -1)) if body.limiter_threshold_db is None else body.limiter_threshold_db
    if not 0 <= volume <= 100:
        raise HTTPException(400, "volume must be between 0 and 100")
    if not -12 <= limiter_threshold_db <= 0:
        raise HTTPException(400, "limiter_threshold_db must be between -12 and 0")
    storage.set_setting("master_volume", str(volume))
    storage.set_setting("master_muted", "1" if muted else "0")
    storage.set_setting("master_limiter_enabled", "1" if limiter_enabled else "0")
    storage.set_setting("master_limiter_threshold_db", str(limiter_threshold_db))
    engine_applied = await orchestrator.apply_master(volume, muted, limiter_enabled, limiter_threshold_db)
    await _broadcast_master()
    await _broadcast_engine_status()
    return {"ok": True, "engine_applied": engine_applied}


@app.post("/api/panic")
async def panic():
    storage.set_setting("master_muted", "1")
    engine_applied = await orchestrator.panic(storage.list_pads())
    await _broadcast_master()
    await _broadcast_sequencer()
    await _broadcast_metronome()
    await _broadcast_looper()
    await _broadcast_engine_status()
    return {"ok": True, "engine_applied": engine_applied}


@app.get("/api/engine/status")
async def get_engine_status():
    return await asyncio.to_thread(orchestrator.engine_status)


@app.post("/api/engine/restart")
async def restart_engine():
    status = await orchestrator.restart_engine(
        storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    await _broadcast_master()
    await _broadcast_engine_status()
    return {"ok": True, "status": status}


@app.post("/api/system/restart", status_code=202)
def restart_full_system():
    """Schedules the user-systemd unit that restarts audio and DiakoPad."""
    try:
        subprocess.Popen(["systemctl", "--user", "start", "diakopad-restart.service"])
    except OSError as exc:
        logger.exception("Could not start the DiakoPad restart unit")
        raise HTTPException(503, "Não foi possível iniciar a recuperação do sistema.") from exc
    return {"ok": True}


@app.get("/api/sounds")
def get_sounds():
    return storage.list_samples()


@app.get("/api/sounds/browse")
def browse_sounds(folder: str = ""):
    try:
        return storage.browse_samples(folder)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/sounds/upload")
async def upload_sound(file: UploadFile, folder: str = Form("")):
    try:
        folder = storage.normalize_folder(folder)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    original_name = Path(file.filename or "sample").name
    ext = Path(original_name).suffix.lower()
    if ext not in storage.ALLOWED_SAMPLE_EXTENSIONS:
        raise HTTPException(400, f"unsupported file type: {ext or '(none)'}")

    display_name = Path(original_name).stem
    safe_stem = storage.SAFE_NAME_RE.sub("_", display_name) or "sample"
    stored_filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{ext}"
    dest = SAMPLES_DIR / stored_filename

    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    sample_id = storage.add_sample(stored_filename, display_name, folder)
    await _broadcast_sounds()
    return {"id": sample_id, "filename": stored_filename, "display_name": display_name, "folder": folder}


@app.get("/api/sounds/{sample_id}/audio")
def get_sound_audio(sample_id: int):
    samples = {s["id"]: s for s in storage.list_samples()}
    sample = samples.get(sample_id)
    if not sample:
        raise HTTPException(404, "sample not found")
    path = SAMPLES_DIR / sample["filename"]
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(path)


@app.delete("/api/sounds/{sample_id}")
async def delete_sound(sample_id: int):
    filename = storage.delete_sample(sample_id)
    if filename is None:
        raise HTTPException(409, "sample not found or still assigned to a pad")
    path = SAMPLES_DIR / filename
    if path.exists():
        path.unlink()
    await _broadcast_sounds()
    return {"ok": True}


@app.get("/api/effects/catalog")
def get_effects_catalog():
    return effects_catalog.list_catalog()


@app.get("/api/pad-effects")
def get_pad_effects():
    return storage.list_pad_effects()


@app.post("/api/pads/{pad_number}/effects/{slot_index}")
async def set_pad_effect_slot(pad_number: int, slot_index: int, body: EffectSlotRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 1 <= slot_index <= storage.EFFECT_SLOTS_PER_PAD:
        raise HTTPException(400, f"slot_index must be between 1 and {storage.EFFECT_SLOTS_PER_PAD}")
    if body.plugin_id is not None and body.plugin_id not in effects_catalog.PLUGIN_CATALOG:
        raise HTTPException(404, "unknown plugin_id")

    storage.set_pad_effect_slot(pad_number, slot_index, body.plugin_id)
    if body.plugin_id:
        for symbol, value in effects_catalog.default_params(body.plugin_id).items():
            storage.set_pad_effect_param(pad_number, slot_index, symbol, value)
    storage.delete_knob_mappings_for_pad_param_prefix(pad_number, f"slot{slot_index}:")

    await orchestrator.apply_pad_effects(pad_number, storage.list_pad_effects())
    await _broadcast_pad_effects()
    await _broadcast_knobs()
    return {"ok": True}


@app.post("/api/pads/{pad_number}/effects/{slot_index}/param")
async def set_pad_effect_param(pad_number: int, slot_index: int, body: EffectParamRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 1 <= slot_index <= storage.EFFECT_SLOTS_PER_PAD:
        raise HTTPException(400, f"slot_index must be between 1 and {storage.EFFECT_SLOTS_PER_PAD}")
    plugin_id = next(
        (
            row["plugin_id"]
            for row in storage.list_pad_effects()
            if row["pad_number"] == pad_number and row["slot_index"] == slot_index
        ),
        None,
    )
    if plugin_id:
        param = next(
            (p for p in effects_catalog.PLUGIN_CATALOG[plugin_id]["params"] if p["symbol"] == body.symbol),
            None,
        )
        if param is None:
            raise HTTPException(400, "unknown param symbol for this slot's plugin")
        if not param["min"] <= body.value <= param["max"]:
            raise HTTPException(400, f"value out of range ({param['min']}..{param['max']})")
    storage.set_pad_effect_param(pad_number, slot_index, body.symbol, body.value)
    await orchestrator.set_effect_param(pad_number, slot_index, body.symbol, body.value)
    await _broadcast_pad_effects()
    return {"ok": True}


# ── Performance scenes ───────────────────────────────────────────────────────


async def _apply_scene_and_broadcast(scene: dict) -> None:
    """Reapplies an already-loaded (storage.load_scene) scene onto the engine
    and broadcasts pads/pad_effects/knobs/tempo/sequencer/metronome - shared
    by the REST load endpoint and the hardware-triggered scene navigation, so
    neither duplicates the other's engine-reload logic. The scene's global
    state (tempo, metronome, transports) is only touched when the scene
    carries one (scenes saved before that joined the snapshot leave it as-is).
    """
    global _pad_notes
    state = scene.get("state")
    if state is not None:
        tempo.set(float(state["sequencer_bpm"]))
        metronome.set_signature(state["metronome_signature"])
        await orchestrator.apply_metronome_style(state["metronome_style"])
    _pad_notes = _build_pad_note_map(storage.list_pads())
    sequencer.load_pattern(storage.list_sequencer_steps())
    await orchestrator.apply_all_pads(
        storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    if state is not None:
        if state["sequencer_running"] == "1":
            await sequencer.start(storage.list_pads(), storage.get_settings(), _on_sequencer_tick)
        else:
            await sequencer.stop()
        if state["metronome_running"] == "1":
            await metronome.start(_on_metronome_beat)
        else:
            await metronome.stop()
    await _broadcast_pads()
    await _broadcast_pad_effects()
    await _broadcast_knobs()
    await _broadcast_tempo()
    await _broadcast_sequencer()
    await _broadcast_metronome()


async def _broadcast_scenes() -> None:
    await manager.broadcast(
        {
            "type": "scenes",
            "scenes": storage.list_scenes(),
            "current_scene_id": storage.get_settings().get("current_scene_id"),
        }
    )


@app.get("/api/scenes")
def list_scenes():
    return storage.list_scenes()


@app.post("/api/scenes")
async def save_scene(body: SceneRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name must not be empty")
    settings = storage.get_settings()
    scene_id = storage.save_scene(
        name,
        storage.list_pads(),
        storage.list_pad_effects(),
        storage.list_knob_mappings(),
        scene_state={
            "sequencer_bpm": settings.get("sequencer_bpm", "100"),
            "metronome_style": settings.get("metronome_style", metronome_sounds.DEFAULT_STYLE),
            "metronome_signature": settings.get("metronome_signature", time_signatures.DEFAULT_SIGNATURE),
            "metronome_running": "1" if metronome.get_state()["running"] else "0",
            "sequencer_running": "1" if sequencer.get_state()["running"] else "0",
        },
        sequencer_steps=storage.list_sequencer_steps(),
    )
    await _broadcast_scenes()
    return {"ok": True, "scene": {"id": scene_id, "name": name}}


class SceneActiveRequest(BaseModel):
    active: bool


@app.post("/api/scenes/{scene_id}/active")
async def set_scene_active(scene_id: int, body: SceneActiveRequest):
    if not storage.set_scene_active(scene_id, body.active):
        raise HTTPException(404, "scene not found")
    await _broadcast_scenes()
    return {"ok": True}


@app.delete("/api/scenes/{scene_id}")
async def delete_scene(scene_id: int):
    if not storage.delete_scene(scene_id):
        raise HTTPException(404, "scene not found")
    await _broadcast_scenes()
    return {"ok": True}


@app.post("/api/scenes/{scene_id}/load")
async def load_scene(scene_id: int):
    scene = storage.load_scene(scene_id)
    if scene is None:
        raise HTTPException(404, "scene not found")

    storage.set_setting("current_scene_id", str(scene_id))
    await _apply_scene_and_broadcast(scene)
    await _broadcast_scenes()
    return {"ok": True, "scene": {"id": scene["id"], "name": scene["name"]}}


# ── Kits (curated sound sets, catalog CRUD - no live WS sync, same as /api/sounds) ──


@app.get("/api/kits")
def list_kits():
    return storage.list_kits()


@app.get("/api/kits/categories")
def list_kit_categories():
    return storage.list_kit_categories()


@app.get("/api/kits/{kit_id}")
def get_kit(kit_id: int):
    kit = storage.get_kit(kit_id)
    if kit is None:
        raise HTTPException(404, "kit not found")
    return kit


@app.delete("/api/kits/{kit_id}")
def delete_kit(kit_id: int):
    if not storage.delete_kit(kit_id):
        raise HTTPException(404, "kit not found")
    return {"ok": True}


@app.post("/api/kits/import")
async def import_kit_pack(file: UploadFile):
    """Uploads a kit pack (.zip, see curate_kit_pack.py) - the primary way to
    add kits on the Pi, from the Config tab, without SSH/laptop access."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
    try:
        kit_ids = kit_import.import_pack(tmp_path)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"kit_ids": kit_ids}


# ── Kit browse mode (touchscreen test endpoints - the real UX is hardware-driven,
#    these exist so the feature is exercisable end-to-end without a physical SMC-PAD) ──


def _require_armed() -> None:
    if _kit_browse_state is None or _kit_browse_state.get("target_pad") is not None:
        raise HTTPException(409, "not armed (call toggle first)")


def _require_browsing() -> None:
    if _kit_browse_state is None or _kit_browse_state.get("target_pad") is None:
        raise HTTPException(409, "not browsing (select a target pad first)")


@app.post("/api/kit-browse/toggle")
async def kit_browse_toggle():
    if _kit_browse_state is None:
        await _kit_browse_arm()
    else:
        await _kit_browse_back()
    return _kit_browse_payload()


@app.post("/api/kit-browse/select-target")
async def kit_browse_select_target(body: KitBrowseSelectTargetRequest):
    _require_armed()
    if not 1 <= body.pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    await _kit_browse_select_target(body.pad_number)
    return _kit_browse_payload()


@app.post("/api/kit-browse/nav")
async def kit_browse_nav(body: KitBrowseNavRequest):
    _require_browsing()
    if body.direction not in ("up", "down", "left", "right"):
        raise HTTPException(400, "direction must be up/down/left/right")
    await _kit_browse_nav(body.direction)
    return _kit_browse_payload()


@app.post("/api/kit-browse/confirm")
async def kit_browse_confirm_endpoint():
    _require_browsing()
    await _kit_browse_confirm()
    return _kit_browse_payload()


@app.post("/api/kit-browse/back")
async def kit_browse_back_endpoint():
    if _kit_browse_state is None:
        raise HTTPException(409, "not browsing")
    await _kit_browse_back()
    return _kit_browse_payload()


@app.post("/api/kit-browse/preview/{pad_number}")
async def kit_browse_preview(pad_number: int):
    """Despite the name (kept for URL stability), this now picks pad_number's
    sound as the browsing candidate - not a bare one-off preview note - since
    kit-browse mode is per-target-pad now: see _kit_browse_select_candidate."""
    _require_browsing()
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    await _kit_browse_select_candidate(pad_number)
    return _kit_browse_payload()


@app.post("/api/settings/kit_browse_preview")
async def set_kit_browse_preview(body: KitBrowsePreviewRequest):
    storage.set_setting("kit_browse_preview_enabled", "1" if body.enabled else "0")
    await _broadcast_settings()
    return {"ok": True}


@app.post("/api/settings/looper_quantize")
async def set_looper_quantize(body: LooperQuantizeRequest):
    storage.set_setting("looper_quantize_enabled", "1" if body.enabled else "0")
    await _broadcast_settings()
    return {"ok": True}


# ── Controller actions (dedicated SMC-PAD controls) ──────────────────────────


@app.get("/api/controller-actions")
def get_controller_actions():
    return {"bindings": storage.list_controller_bindings(), "pending_learn": _pending_controller_learn}


@app.post("/api/controller-actions/{action}/learn")
async def start_controller_learn(action: str):
    global _pending_controller_learn
    if action not in CONTROLLER_ACTIONS:
        raise HTTPException(400, "unknown action")
    _pending_controller_learn = action
    await _broadcast_controller_actions()
    return {"ok": True}


@app.post("/api/controller-actions/learn/cancel")
async def cancel_controller_learn():
    global _pending_controller_learn
    _pending_controller_learn = None
    await _broadcast_controller_actions()
    return {"ok": True}


@app.delete("/api/controller-actions/bindings/{binding_id}")
async def remove_controller_binding(binding_id: int):
    if not storage.delete_controller_binding(binding_id):
        raise HTTPException(404, "binding not found")
    await _broadcast_controller_actions()
    return {"ok": True}


@app.get("/api/sequencer")
def get_sequencer():
    return sequencer.get_state()


@app.post("/api/sequencer/transport")
async def set_sequencer_transport(body: SequencerTransportRequest):
    if body.running:
        await sequencer.start(storage.list_pads(), storage.get_settings(), _on_sequencer_tick)
    else:
        await sequencer.stop()
    await _broadcast_sequencer()
    return {"ok": True}


@app.post("/api/sequencer/steps/{pad_number}/{step_index}")
async def toggle_sequencer_step(pad_number: int, step_index: int, body: SequencerStepRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 0 <= step_index < sequencer.STEP_COUNT:
        raise HTTPException(400, f"step_index must be between 0 and {sequencer.STEP_COUNT - 1}")
    sequencer.toggle_step(pad_number, step_index, body.active)
    await _broadcast_sequencer()
    return {"ok": True}


@app.post("/api/sequencer/clear")
async def clear_sequencer():
    sequencer.clear()
    await _broadcast_sequencer()
    return {"ok": True}


# ── Sequencer patterns ───────────────────────────────────────────────────────


@app.get("/api/patterns")
def list_patterns():
    return storage.list_patterns()


@app.post("/api/patterns")
async def save_pattern(body: PatternRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name must not be empty")
    pattern_id = storage.save_pattern(name, storage.list_sequencer_steps())
    await _broadcast_patterns()
    return {"ok": True, "pattern": {"id": pattern_id, "name": name}}


@app.delete("/api/patterns/{pattern_id}")
async def delete_pattern(pattern_id: int):
    if not storage.delete_pattern(pattern_id):
        raise HTTPException(404, "pattern not found")
    await _broadcast_patterns()
    return {"ok": True}


@app.post("/api/patterns/{pattern_id}/load")
async def load_pattern(pattern_id: int):
    pattern = storage.apply_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(404, "pattern not found")
    sequencer.load_pattern(storage.list_sequencer_steps())
    await _broadcast_sequencer()
    return {"ok": True, "pattern": {"id": pattern["id"], "name": pattern["name"]}}


@app.get("/api/looper")
def get_looper():
    return looper.get_state()


@app.post("/api/looper/record/start")
async def looper_record_start():
    looper.record_start()
    await _broadcast_looper()
    return {"ok": True}


@app.post("/api/looper/record/stop")
async def looper_record_stop():
    await looper.record_stop(storage.list_pads(), storage.get_settings())
    await _broadcast_looper()
    return {"ok": True}


@app.post("/api/looper/stop")
async def looper_stop():
    looper.stop()
    await _broadcast_looper()
    return {"ok": True}


@app.post("/api/looper/clear")
async def looper_clear():
    looper.clear()
    await _broadcast_looper()
    return {"ok": True}


@app.post("/api/looper/overdub/start")
async def looper_overdub_start():
    looper.overdub_start()
    await _broadcast_looper()
    return {"ok": True}


@app.post("/api/looper/overdub/stop")
async def looper_overdub_stop():
    looper.overdub_stop()
    await _broadcast_looper()
    return {"ok": True}


@app.get("/api/tempo")
def get_tempo():
    return {"bpm": tempo.get()}


@app.post("/api/tempo")
async def set_tempo(body: TempoRequest):
    if not tempo.MIN_BPM <= body.bpm <= tempo.MAX_BPM:
        raise HTTPException(400, f"bpm must be between {tempo.MIN_BPM:.0f} and {tempo.MAX_BPM:.0f}")
    tempo.set(body.bpm)
    await _broadcast_tempo()
    return {"ok": True}


@app.get("/api/metronome/styles")
def get_metronome_styles():
    return metronome_sounds.list_styles()


@app.get("/api/metronome/time-signatures")
def get_metronome_time_signatures():
    return time_signatures.list_signatures()


@app.get("/api/metronome")
def get_metronome():
    return {
        **metronome.get_state(),
        "style": storage.get_settings().get("metronome_style", metronome_sounds.DEFAULT_STYLE),
    }


@app.post("/api/metronome/transport")
async def set_metronome_transport(body: MetronomeTransportRequest):
    if body.running:
        await metronome.start(_on_metronome_beat)
    else:
        await metronome.stop()
    await _broadcast_metronome()
    return {"ok": True}


@app.post("/api/metronome/style")
async def set_metronome_style(body: MetronomeStyleRequest):
    if body.style not in metronome_sounds.STYLES:
        raise HTTPException(400, "unknown metronome style")
    async with _metronome_style_lock:
        engine_applied = await orchestrator.apply_metronome_style(body.style)
        storage.set_setting("metronome_style", body.style)
        await _broadcast_metronome()
    return {"ok": True, "engine_applied": engine_applied}


@app.post("/api/metronome/signature")
async def set_metronome_signature(body: MetronomeSignatureRequest):
    if body.signature not in time_signatures.SIGNATURES:
        raise HTTPException(400, "unknown time signature")
    storage.set_setting("metronome_signature", body.signature)
    metronome.set_signature(body.signature)
    await _broadcast_metronome()
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "pads", "pads": _pads_payload()})
        await ws.send_json({"type": "sounds", "sounds": storage.list_samples()})
        await ws.send_json({"type": "knobs", "knobs": _enriched_knob_mappings(), "pending_learn": _pending_learn})
        await ws.send_json({"type": "settings", "settings": _settings_payload()})
        await ws.send_json({"type": "pad_effects", "pad_effects": storage.list_pad_effects()})
        await ws.send_json({"type": "sequencer", **sequencer.get_state()})
        await ws.send_json({"type": "looper", **looper.get_state()})
        await ws.send_json({"type": "tempo", "bpm": tempo.get()})
        await ws.send_json({"type": "master", **orchestrator.master_state()})
        await ws.send_json({"type": "engine_status", **(await asyncio.to_thread(orchestrator.engine_status))})
        await ws.send_json(
            {
                "type": "scenes",
                "scenes": storage.list_scenes(),
                "current_scene_id": storage.get_settings().get("current_scene_id"),
            }
        )
        await ws.send_json({"type": "patterns", "patterns": storage.list_patterns()})
        await ws.send_json({"type": "note_learn", "pending_pad": _pending_note_learn})
        await ws.send_json(
            {
                "type": "controller_actions",
                "bindings": storage.list_controller_bindings(),
                "pending_learn": _pending_controller_learn,
            }
        )
        await ws.send_json(
            {
                "type": "metronome",
                **metronome.get_state(),
                "style": storage.get_settings().get("metronome_style", metronome_sounds.DEFAULT_STYLE),
            }
        )
        await ws.send_json(_kit_browse_payload())
        while True:
            await ws.receive_text()  # client doesn't send anything meaningful; just keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

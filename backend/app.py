"""DiakoPad web app: pad grid + sound library, served to the touchscreen and
to any browser on the LAN. Owns its own audio engine directly - one sfizz
instance per pad, per-pad configurable mod-host effect chains, a step
sequencer, a live looper and a metronome - orchestrated by
engine/orchestrator.py."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import midi
import storage
from engine import effects_catalog, knob_registry, looper, metronome, metronome_sounds, orchestrator, sequencer, tempo

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


class MetronomeBeatsPerBarRequest(BaseModel):
    beats_per_bar: int


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

# --- Knob MIDI-learn state -------------------------------------------------
# The MIDI input callback fires on mido/rtmidi's own thread; everything here
# only ever runs on the main asyncio loop, reached via call_soon_threadsafe.
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_learn: Optional[dict] = None  # {"scope": str, "pad_number": int|None, "param": str}
_pending_cc_values: dict[tuple[str, Optional[int], str], int] = {}
_debounce_handles: dict[tuple[str, Optional[int], str], asyncio.TimerHandle] = {}
KNOB_DEBOUNCE_SECONDS = 0.3


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
    """Feeds the live looper's recorder. Only ever sees genuine hardware hits
    - sequencer/looper-triggered notes go out DiakoPad-trigger-out straight
    into each pad's sfizz instance, never back through this input port."""
    if looper.get_state()["state"] != "recording":
        return
    pad = next((p for p in storage.list_pads() if p["midi_note"] == note), None)
    if pad is not None:
        looper.record_event(pad["pad_number"], velocity)


def _handle_cc(control: int, value: int) -> None:
    global _pending_learn
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
    global _main_loop
    storage.init_db()
    _main_loop = asyncio.get_event_loop()
    midi.open_input(_on_midi_cc, _on_midi_note)
    await orchestrator.startup()
    await orchestrator.apply_all_pads(storage.list_pads(), storage.get_settings(), storage.list_pad_effects())
    sequencer.load_pattern(storage.list_sequencer_steps())
    tempo.load()
    metronome.set_beats_per_bar(int(storage.get_settings().get("metronome_beats_per_bar", 4)))
    await orchestrator.apply_metronome_style(storage.get_settings().get("metronome_style", metronome_sounds.DEFAULT_STYLE))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await sequencer.stop()
    await metronome.stop()
    looper.stop()
    orchestrator.shutdown()


def _pads_payload() -> list[dict]:
    pads = storage.list_pads()
    for p in pads:
        p["has_sample"] = p["sample_id"] is not None
    return pads


async def _broadcast_pads() -> None:
    await manager.broadcast({"type": "pads", "pads": _pads_payload()})


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


@app.post("/api/pads/{pad_number}/note")
async def set_pad_note(pad_number: int, body: NoteRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 0 <= body.midi_note <= 127:
        raise HTTPException(400, "midi_note must be between 0 and 127")
    storage.set_pad_note(pad_number, body.midi_note)
    await orchestrator.apply_pad(
        pad_number, storage.list_pads(), storage.get_settings(), storage.list_pad_effects()
    )
    await _broadcast_pads()
    return {"ok": True}


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


@app.get("/api/sounds")
def get_sounds():
    return storage.list_samples()


@app.get("/api/sounds/browse")
def browse_sounds(folder: str = ""):
    return storage.browse_samples(folder)


@app.post("/api/sounds/upload")
async def upload_sound(file: UploadFile, folder: str = Form("")):
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
    storage.set_pad_effect_param(pad_number, slot_index, body.symbol, body.value)
    await orchestrator.set_effect_param(pad_number, slot_index, body.symbol, body.value)
    await _broadcast_pad_effects()
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


@app.post("/api/metronome/beats-per-bar")
async def set_metronome_beats_per_bar(body: MetronomeBeatsPerBarRequest):
    if not 1 <= body.beats_per_bar <= 12:
        raise HTTPException(400, "beats_per_bar must be between 1 and 12")
    storage.set_setting("metronome_beats_per_bar", str(body.beats_per_bar))
    metronome.set_beats_per_bar(body.beats_per_bar)
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
        await ws.send_json(
            {
                "type": "metronome",
                **metronome.get_state(),
                "style": storage.get_settings().get("metronome_style", metronome_sounds.DEFAULT_STYLE),
            }
        )
        while True:
            await ws.receive_text()  # client doesn't send anything meaningful; just keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

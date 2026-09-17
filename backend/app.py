"""DiakoPad web app: pad grid + sound library, served to the touchscreen and
to any browser on the LAN. Talks to Zynthian's Sfizz engine indirectly, by
regenerating an .sfz kit (sfz.py) and sending a Program Change (midi.py)."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import midi
import sfz
import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diakopad")

BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
SAMPLES_DIR = BASE_DIR / "samples"
SAMPLES_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif"}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

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
    pad_number: int
    param: str  # "volume" | "pan" | "cutoff"


class SettingsRequest(BaseModel):
    sustain_mode: Optional[bool] = None
    velocity_sensitive: Optional[bool] = None


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

# --- Knob MIDI-learn state -------------------------------------------------
# The MIDI input callback fires on mido/rtmidi's own thread; everything here
# only ever runs on the main asyncio loop, reached via call_soon_threadsafe.
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_learn: Optional[dict] = None  # {"pad_number": int, "param": str}
_pending_cc_values: dict[tuple[int, str], int] = {}
_debounce_handles: dict[tuple[int, str], asyncio.TimerHandle] = {}
KNOB_DEBOUNCE_SECONDS = 0.3


def _frac_to_cutoff(frac: float) -> Optional[float]:
    """0..1 -> 200Hz..20kHz log scale. >=0.99 means "fully open" (no filter)."""
    if frac >= 0.99:
        return None
    return round(200 * (20000 / 200) ** frac)


def _cc_to_volume_db(v: int) -> float:
    return round(-24 + (v / 127) * 36, 1)  # -24..+12 dB


def _cc_to_pan(v: int) -> float:
    return round(-100 + (v / 127) * 200)  # -100..100


def _cc_to_cutoff(v: int) -> Optional[float]:
    return _frac_to_cutoff(v / 127)


def _on_midi_cc(control: int, value: int) -> None:
    if _main_loop is not None:
        _main_loop.call_soon_threadsafe(_handle_cc, control, value)


def _handle_cc(control: int, value: int) -> None:
    global _pending_learn
    if _pending_learn is not None:
        storage.set_knob_mapping(control, _pending_learn["pad_number"], _pending_learn["param"])
        _pending_learn = None
        asyncio.create_task(_broadcast_knobs())
        return

    target = storage.get_knob_target(control)
    if target is None:
        return
    key = (target["pad_number"], target["param"])
    _pending_cc_values[key] = value
    old_handle = _debounce_handles.get(key)
    if old_handle is not None:
        old_handle.cancel()
    _debounce_handles[key] = _main_loop.call_later(
        KNOB_DEBOUNCE_SECONDS, lambda: asyncio.create_task(_apply_knob_target(key))
    )


async def _apply_knob_target(key: tuple[int, str]) -> None:
    _debounce_handles.pop(key, None)
    value = _pending_cc_values.pop(key, None)
    if value is None:
        return
    pad_number, param = key
    if param == "volume":
        storage.set_pad_mix(pad_number, volume_db=_cc_to_volume_db(value))
    elif param == "pan":
        storage.set_pad_mix(pad_number, pan=_cc_to_pan(value))
    elif param == "cutoff":
        storage.set_pad_mix(pad_number, cutoff_hz=_cc_to_cutoff(value))
    _regen_kit(storage.list_pads())
    await _broadcast_pads()


# --- Startup -----------------------------------------------------------


@app.on_event("startup")
def on_startup() -> None:
    global _main_loop
    storage.init_db()
    midi.ensure_open()
    _main_loop = asyncio.get_event_loop()
    midi.open_input(_on_midi_cc)


def _pads_payload() -> list[dict]:
    pads = storage.list_pads()
    for p in pads:
        p["has_sample"] = p["sample_id"] is not None
    return pads


async def _broadcast_pads() -> None:
    await manager.broadcast({"type": "pads", "pads": _pads_payload()})


async def _broadcast_sounds() -> None:
    await manager.broadcast({"type": "sounds", "sounds": storage.list_samples()})


async def _broadcast_knobs() -> None:
    await manager.broadcast(
        {"type": "knobs", "knobs": storage.list_knob_mappings(), "pending_learn": _pending_learn}
    )


async def _broadcast_settings() -> None:
    await manager.broadcast({"type": "settings", "settings": storage.get_settings()})


def _regen_kit(pads: list[dict]) -> tuple[str, bool]:
    filename, preset_index = sfz.write_next_kit(pads, storage.get_settings())
    sent = midi.send_program_change(preset_index)
    return filename, sent


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
    filename, sent = _regen_kit(storage.list_pads())

    await _broadcast_pads()
    return {"ok": True, "kit_file": filename, "midi_sent": sent}


@app.post("/api/pads/{pad_number}/note")
async def set_pad_note(pad_number: int, body: NoteRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 0 <= body.midi_note <= 127:
        raise HTTPException(400, "midi_note must be between 0 and 127")
    storage.set_pad_note(pad_number, body.midi_note)
    _regen_kit(storage.list_pads())
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
    _regen_kit(storage.list_pads())
    await _broadcast_pads()
    return {"ok": True}


@app.get("/api/knobs")
def get_knobs():
    return {"knobs": storage.list_knob_mappings(), "pending_learn": _pending_learn}


@app.post("/api/knobs/learn")
async def start_knob_learn(body: LearnRequest):
    global _pending_learn
    if not 1 <= body.pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if body.param not in ("volume", "pan", "cutoff"):
        raise HTTPException(400, "param must be volume, pan or cutoff")
    _pending_learn = {"pad_number": body.pad_number, "param": body.param}
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
    return storage.get_settings()


@app.post("/api/settings")
async def update_settings(body: SettingsRequest):
    if body.sustain_mode is not None:
        storage.set_setting("sustain_mode", "1" if body.sustain_mode else "0")
    if body.velocity_sensitive is not None:
        storage.set_setting("velocity_sensitive", "1" if body.velocity_sensitive else "0")
    _regen_kit(storage.list_pads())
    await _broadcast_settings()
    return {"ok": True}


@app.get("/api/sounds")
def get_sounds():
    return storage.list_samples()


@app.post("/api/sounds/upload")
async def upload_sound(file: UploadFile):
    original_name = Path(file.filename or "sample").name
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"unsupported file type: {ext or '(none)'}")

    display_name = Path(original_name).stem
    safe_stem = SAFE_NAME_RE.sub("_", display_name) or "sample"
    stored_filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{ext}"
    dest = SAMPLES_DIR / stored_filename

    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    sample_id = storage.add_sample(stored_filename, display_name)
    await _broadcast_sounds()
    return {"id": sample_id, "filename": stored_filename, "display_name": display_name}


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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "pads", "pads": _pads_payload()})
        await ws.send_json({"type": "sounds", "sounds": storage.list_samples()})
        await ws.send_json(
            {"type": "knobs", "knobs": storage.list_knob_mappings(), "pending_learn": _pending_learn}
        )
        await ws.send_json({"type": "settings", "settings": storage.get_settings()})
        while True:
            await ws.receive_text()  # client doesn't send anything meaningful; just keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

"""DiakoPad web app: pad grid + sound library, served to the touchscreen and
to any browser on the LAN. Talks to Zynthian's Sfizz engine indirectly, by
regenerating an .sfz kit (sfz.py) and sending a Program Change (midi.py)."""
from __future__ import annotations

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


@app.on_event("startup")
def on_startup() -> None:
    storage.init_db()


def _pads_payload() -> list[dict]:
    pads = storage.list_pads()
    for p in pads:
        p["has_sample"] = p["sample_id"] is not None
    return pads


async def _broadcast_pads() -> None:
    await manager.broadcast({"type": "pads", "pads": _pads_payload()})


async def _broadcast_sounds() -> None:
    await manager.broadcast({"type": "sounds", "sounds": storage.list_samples()})


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
    pads = storage.list_pads()
    filename, preset_index = sfz.write_next_kit(pads)
    sent = midi.send_program_change(preset_index)

    await _broadcast_pads()
    return {"ok": True, "kit_file": filename, "midi_sent": sent}


@app.post("/api/pads/{pad_number}/note")
async def set_pad_note(pad_number: int, body: NoteRequest):
    if not 1 <= pad_number <= 16:
        raise HTTPException(400, "pad_number must be between 1 and 16")
    if not 0 <= body.midi_note <= 127:
        raise HTTPException(400, "midi_note must be between 0 and 127")
    storage.set_pad_note(pad_number, body.midi_note)
    pads = storage.list_pads()
    filename, preset_index = sfz.write_next_kit(pads)
    midi.send_program_change(preset_index)
    await _broadcast_pads()
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
        while True:
            await ws.receive_text()  # client doesn't send anything meaningful; just keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

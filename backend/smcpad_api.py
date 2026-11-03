"""API HTTP da aba "SMCPAD Config" (leitura/edição do M-Vave SMC-PAD).

As rotas ficam atrás do mesmo controle de acesso do resto da API (token/loopback).
A gravação no aparelho é opt-in: `smcpad_write_enabled` nas configurações, desligada
por padrão. Veja smcpad_device.py para as regras de segurança da escrita.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import smcpad as sp
import smcpad_device as dev
import storage

BACKUP_DIR = Path(os.environ.get("DIAKOPAD_SMCPAD_BACKUP_DIR", Path(__file__).parent / "smcpad_backups"))
PORT_PATTERN = os.environ.get("DIAKOPAD_SMCPAD_PORT_PATTERN", dev.DEFAULT_PORT_PATTERN)
WRITE_SETTING = "smcpad_write_enabled"
MAX_IMPORT_BYTES = sp.PRESET_SIZE

router = APIRouter(prefix="/api/smcpad", tags=["smcpad"])
_service: Optional[dev.SmcPadService] = None


def _write_enabled() -> bool:
    return storage.get_settings().get(WRITE_SETTING, "0") == "1"


def get_service() -> dev.SmcPadService:
    global _service
    if _service is None:
        _service = dev.SmcPadService(lambda: dev.MidoTransport(PORT_PATTERN), BACKUP_DIR, _write_enabled)
    return _service


def set_service(service: Optional[dev.SmcPadService]) -> None:
    """Injeta um serviço (testes) ou restaura o padrão (None)."""
    global _service
    _service = service


class EditsRequest(BaseModel):
    edits: list[dict[str, Any]]
    commit: bool = True


class WriteModeRequest(BaseModel):
    enabled: bool


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, dev.WriteDisabled):
        return HTTPException(403, str(exc))
    if isinstance(exc, (sp.EditError, sp.ProtocolError)):
        return HTTPException(422, str(exc))
    if isinstance(exc, dev.VerifyError):
        return HTTPException(502, f"{exc}. Os bytes originais foram restaurados e nada foi gravado de vez.")
    return HTTPException(503, str(exc))


@router.get("/status")
def status() -> dict[str, Any]:
    service = get_service()
    try:
        return service.status()
    except dev.DeviceError as exc:
        return {"connected": False, "error": str(exc), "write_enabled": service.write_enabled()}


@router.get("/config")
def config(preset: Optional[int] = None) -> dict[str, Any]:
    service = get_service()
    try:
        snap = service.snapshot()
    except dev.DeviceError as exc:
        raise _fail(exc) from exc
    current = snap.current_preset
    chosen = current if preset is None else preset
    if not 1 <= chosen <= sp.PRESET_COUNT:
        raise HTTPException(422, f"preset deve estar em 1..{sp.PRESET_COUNT}")
    return {
        "identity": snap.identity,
        "write_enabled": service.write_enabled(),
        "current_preset": current,
        "globals": snap.globals,
        "calibration": sp.parse_calibration(snap.calibration),
        "preset_number": chosen,
        "preset": sp.parse_preset(sp.preset_slice(snap.config, chosen)),
        "enums": sp.enums(),
        "limits": {name: {k: {"min": s["lo"] + s["shift"], "max": s["hi"] + s["shift"]} for k, s in fields.items()} for name, fields in sp.FIELDS.items()},
    }


@router.post("/edits")
def edits(body: EditsRequest) -> dict[str, Any]:
    try:
        return get_service().apply_edits(body.edits, commit=body.commit)
    except (dev.DeviceError, sp.EditError, sp.ProtocolError) as exc:
        raise _fail(exc) from exc


@router.post("/write-enabled")
def write_enabled(body: WriteModeRequest) -> dict[str, bool]:
    storage.set_setting(WRITE_SETTING, "1" if body.enabled else "0")
    return {"write_enabled": body.enabled}


@router.get("/preset/{preset}/export")
def export_preset(preset: int) -> Response:
    try:
        data = get_service().export_preset(preset)
    except (dev.DeviceError, sp.EditError, sp.ProtocolError) as exc:
        raise _fail(exc) from exc
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="smcpad-preset{preset}.spc"'})


@router.post("/preset/{preset}/import")
async def import_preset(preset: int, request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > MAX_IMPORT_BYTES:
        raise HTTPException(413, f"arquivo maior que {MAX_IMPORT_BYTES} bytes")
    try:
        return await asyncio.to_thread(get_service().import_preset, preset, body)
    except (dev.DeviceError, sp.EditError, sp.ProtocolError) as exc:
        raise _fail(exc) from exc


@router.get("/backups")
def backups() -> list[dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("smcpad-*.bin"), reverse=True)
    return [{"name": f.name, "bytes": f.stat().st_size, "modified": f.stat().st_mtime} for f in files]

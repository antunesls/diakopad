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
import smcpad_colors
import smcpad_device as dev
import storage

BACKUP_DIR = Path(os.environ.get("DIAKOPAD_SMCPAD_BACKUP_DIR", Path(__file__).parent / "smcpad_backups"))
PORT_PATTERN = os.environ.get("DIAKOPAD_SMCPAD_PORT_PATTERN", dev.DEFAULT_PORT_PATTERN)
WRITE_SETTING = "smcpad_write_enabled"
SCENE_COLORS_SETTING = "smcpad_scene_colors"
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


def scene_colors_enabled() -> bool:
    return storage.get_settings().get(SCENE_COLORS_SETTING, "1") == "1"


def capture_scene_colors(scene_id: int) -> bool:
    """Lê as cores dos pads do aparelho e guarda na cena. Levanta DeviceError se o aparelho não responde
    (nesse caso as cores antigas da cena ficam como estavam)."""
    return storage.set_scene_smc_colors(scene_id, get_service().read_pad_colors())


def generate_scene_colors(scene_id: int, *, apply: bool = False) -> Optional[dict[str, Any]]:
    """Gera e guarda o kit de cores da cena pelo tipo de som de cada pad. Os 112 registros que o kit não
    toca partem das cores já salvas na cena; sem elas, das cores atuais do aparelho (se responder); sem
    aparelho, de um fundo escuro. Com apply=True pinta também o aparelho (ao vivo). None: cena inexistente."""
    sounds = storage.scene_pad_sounds(scene_id)
    if sounds is None:
        return None
    base = storage.get_scene_smc_colors(scene_id)
    if base is None:
        try:
            base = get_service().read_pad_colors()
        except Exception:  # aparelho ausente: o kit não depende dele
            base = None
    colors, detail = smcpad_colors.kit_colors(sounds, base)
    storage.set_scene_smc_colors(scene_id, colors)
    result: dict[str, Any] = {"ok": True, "pads": detail, "applied": False}
    if apply:
        try:
            get_service().apply_pad_colors(colors)
            result["applied"] = True
        except Exception as exc:
            result["apply_error"] = str(exc)
    return result


def apply_scene_colors(scene_id: int) -> Optional[dict[str, Any]]:
    """Pinta o aparelho (ao vivo, sem gravar de vez) com as cores da cena; None se a cena não tem cores."""
    colors = storage.get_scene_smc_colors(scene_id)
    return None if colors is None else get_service().apply_pad_colors(colors)


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


# -- cores por cena -------------------------------------------------------------

def _scene_or_404(scene_id: int) -> dict[str, Any]:
    scene = storage.get_scene(scene_id)
    if scene is None:
        raise HTTPException(404, "scene not found")
    return scene


@router.get("/scene-colors")
def scene_colors() -> dict[str, Any]:
    return {
        "enabled": scene_colors_enabled(),
        "scenes": [{"id": s["id"], "name": s["name"], "has_colors": bool(s["has_smc_colors"])} for s in storage.list_scenes()],
        "legend": smcpad_colors.legend(),
    }


@router.post("/scene-colors/enabled")
def scene_colors_enabled_setting(body: WriteModeRequest) -> dict[str, bool]:
    storage.set_setting(SCENE_COLORS_SETTING, "1" if body.enabled else "0")
    return {"enabled": body.enabled}


@router.get("/scenes/{scene_id}/colors")
def get_scene_colors(scene_id: int) -> dict[str, Any]:
    scene = _scene_or_404(scene_id)
    colors = storage.get_scene_smc_colors(scene_id)
    if colors is None:
        raise HTTPException(404, "esta cena não tem cores do SMC-PAD salvas")
    return {"scene": {"id": scene["id"], "name": scene["name"]}, "colors": colors}


@router.post("/scenes/{scene_id}/colors/capture")
def capture_colors(scene_id: int) -> dict[str, Any]:
    scene = _scene_or_404(scene_id)
    try:
        capture_scene_colors(scene_id)
    except (dev.DeviceError, sp.ProtocolError) as exc:
        raise _fail(exc) from exc
    return {"ok": True, "scene": {"id": scene["id"], "name": scene["name"]}}


@router.post("/scenes/{scene_id}/colors/apply")
def apply_colors(scene_id: int) -> dict[str, Any]:
    _scene_or_404(scene_id)
    try:
        result = apply_scene_colors(scene_id)
    except (dev.DeviceError, sp.EditError, sp.ProtocolError) as exc:
        raise _fail(exc) from exc
    if result is None:
        raise HTTPException(404, "esta cena não tem cores do SMC-PAD salvas")
    return {"ok": True, **result}


@router.delete("/scenes/{scene_id}/colors")
def delete_colors(scene_id: int) -> dict[str, bool]:
    _scene_or_404(scene_id)
    return {"ok": storage.delete_scene_smc_colors(scene_id)}


@router.post("/scenes/{scene_id}/colors/generate")
def generate_colors(scene_id: int, apply: bool = False) -> dict[str, Any]:
    """Kit de cores pelo tipo de som dos pads da cena (bumbo azul, caixa vermelha...)."""
    _scene_or_404(scene_id)
    result = generate_scene_colors(scene_id, apply=apply)
    if result is None:  # pragma: no cover - a cena sumiu entre as duas consultas
        raise HTTPException(404, "scene not found")
    return result


@router.post("/scene-colors/generate-all")
def generate_all_colors(apply_current: Optional[int] = None) -> dict[str, Any]:
    """Gera o kit de cores de TODAS as cenas. apply_current: id de uma cena para pintar também o aparelho."""
    done = []
    for scene in storage.list_scenes():
        result = generate_scene_colors(scene["id"], apply=scene["id"] == apply_current)
        counts: dict[str, int] = {}
        for pad in (result or {}).get("pads", []):
            counts[pad["label"]] = counts.get(pad["label"], 0) + 1
        done.append({"id": scene["id"], "name": scene["name"], "types": counts, "applied": bool(result and result.get("applied"))})
    return {"ok": True, "scenes": done}

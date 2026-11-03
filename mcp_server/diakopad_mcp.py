"""Servidor MCP do DiakoPad.

Expõe para um agente (Claude Code, Claude Desktop, ...) tudo o que a interface
web faz: pads, mix, efeitos, biblioteca de sons, kits, cenas, sequencer,
looper, metrônomo, master, mapeamentos MIDI e manutenção do motor.

É um processo stdio separado e só fala com a API REST do DiakoPad, então pode
rodar no próprio laptop/Pi ou em outra máquina da rede:

    DIAKOPAD_URL    URL do DiakoPad (padrão http://localhost:8080)
    DIAKOPAD_TOKEN  o mesmo DIAKOPAD_TOKEN do serviço, se o acesso for remoto
                    (no próprio laptop o loopback dispensa token)
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal, Optional
from urllib.parse import unquote, urlparse

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

BASE_URL = os.environ.get("DIAKOPAD_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DIAKOPAD_TOKEN", "")
AUTH_COOKIE = "diakopad_token"  # mesmo nome de app.AUTH_COOKIE

ALLOWED_SAMPLE_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif"}
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # mesmo limite de upload do backend

PadNumber = Annotated[int, Field(ge=1, le=16, description="Número do pad (1-16)")]
EffectSlot = Annotated[int, Field(ge=1, le=3, description="Slot de efeito do pad (1-3)")]
StepIndex = Annotated[int, Field(ge=0, le=15, description="Passo do sequencer (0-15)")]
TrackIndex = Annotated[int, Field(ge=0, le=3, description="Trilha do looper (0-3)")]
Source = Annotated[str, Field(description="Caminho de arquivo local (na máquina do MCP) ou URL http(s)")]

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
FETCHES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

mcp = MCPServer(
    name="diakopad",
    title="DiakoPad",
    instructions=(
        "Controla o DiakoPad, uma estação de 16 drum pads usada ao vivo. "
        "Comece com get_overview para ver o estado atual. Sons (samples) ficam "
        "numa biblioteca e são atribuídos a pads; kits são conjuntos nomeados "
        "pad->sample; cenas guardam o set inteiro (pads, mix, efeitos, knobs, "
        "BPM, metrônomo, sequencer). Mudanças valem imediatamente no áudio: "
        "durante um show, prefira mudanças pequenas e confirme antes de ações "
        "destrutivas. Use panic para silenciar tudo numa emergência. "
        "O controlador SMC-PAD tem a própria configuração interna (pads, botões, "
        "knobs, globais, calibração): comece com smcpad_status e smcpad_get_config; "
        "smcpad_edit grava no aparelho e só funciona se o usuário ligou "
        "\"Gravação\" na aba SMCPAD Config."
    ),
)

_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        cookies = {AUTH_COOKIE: TOKEN} if TOKEN else None
        _client = httpx.AsyncClient(base_url=BASE_URL, cookies=cookies, timeout=60.0)
    return _client


def set_http_client(client: httpx.AsyncClient) -> None:
    """Permite injetar um cliente (testes)."""
    global _client
    _client = client


async def _call(method: str, path: str, **kwargs: Any) -> Any:
    try:
        res = await _http().request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise ToolError(f"DiakoPad inacessível em {BASE_URL}: {exc}") from exc
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", res.text)
        except ValueError:
            detail = res.text
        raise ToolError(f"DiakoPad respondeu {res.status_code}: {detail}")
    if not res.content:
        return {"ok": True}
    try:
        return res.json()
    except ValueError:
        return {"ok": True, "response": res.text}


async def _get(path: str, **kwargs: Any) -> Any:
    return await _call("GET", path, **kwargs)


async def _post(path: str, body: Optional[dict] = None, **kwargs: Any) -> Any:
    return await _call("POST", path, json=body if body is not None else {}, **kwargs)


async def _delete(path: str) -> Any:
    return await _call("DELETE", path)


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# ── Arquivos de áudio (caminho local ou URL) ─────────────────────────────────


def _is_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def _check_extension(name: str) -> None:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_SAMPLE_EXTENSIONS:
        raise ToolError(f"tipo de arquivo não suportado: {ext or '(sem extensão)'} "
                        f"(aceitos: {', '.join(sorted(ALLOWED_SAMPLE_EXTENSIONS))})")


async def _read_source(source: str, allowed_ext: Optional[set[str]] = None) -> tuple[str, bytes]:
    """Retorna (nome do arquivo, conteúdo) de um caminho local ou URL."""
    if _is_url(source):
        name = Path(unquote(urlparse(source).path)).name or "sample"
        data = bytearray()
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as dl:
                async with dl.stream("GET", source) as res:
                    if res.status_code >= 400:
                        raise ToolError(f"download falhou ({res.status_code}): {source}")
                    async for chunk in res.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_DOWNLOAD_BYTES:
                            raise ToolError("arquivo maior que 200 MB")
        except httpx.HTTPError as exc:
            raise ToolError(f"download falhou: {exc}") from exc
        content = bytes(data)
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise ToolError(f"arquivo não encontrado: {path}")
        name = path.name
        content = path.read_bytes()
    if allowed_ext is None:
        _check_extension(name)
    elif Path(name).suffix.lower() not in allowed_ext:
        raise ToolError(f"esperado arquivo {', '.join(sorted(allowed_ext))}: {name}")
    return name, content


# ── Visão geral / motor ──────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def get_overview() -> dict:
    """Estado geral do DiakoPad numa chamada: pads, master, BPM, metrônomo,
    sequencer, looper, configurações, cenas e status do motor de áudio."""
    return {
        "pads": await _get("/api/pads"),
        "master": await _get("/api/master"),
        "tempo": await _get("/api/tempo"),
        "metronome": await _get("/api/metronome"),
        "sequencer": await _get("/api/sequencer"),
        "looper": await _get("/api/looper"),
        "settings": await _get("/api/settings"),
        "scenes": await _get("/api/scenes"),
        "fx_buses": await _get("/api/fx-buses"),
        "engine": await _get("/api/engine/status"),
    }


@mcp.tool(annotations=READ)
async def get_engine_status() -> dict:
    """Status do motor de áudio (JACK/PipeWire, sfizz, mod-host, MIDI)."""
    return await _get("/api/engine/status")


@mcp.tool(annotations=DESTRUCTIVE)
async def restart_engine() -> dict:
    """Reinicia o motor de áudio (sfizz/mod-host). Gera um corte no som."""
    return await _post("/api/engine/restart")


@mcp.tool(annotations=DESTRUCTIVE)
async def restart_system() -> dict:
    """Reinicia áudio (PipeWire) e o serviço DiakoPad inteiro. O DiakoPad fica
    fora do ar por alguns segundos. Use só para recuperar de falhas."""
    return await _post("/api/system/restart")


@mcp.tool(annotations=DESTRUCTIVE)
async def panic() -> dict:
    """PANIC: muta o master e para sequencer, metrônomo, looper e notas ativas.
    Para voltar a ouvir, use set_master(muted=False)."""
    return await _post("/api/panic")


# ── Master ───────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def get_master() -> dict:
    """Volume master, mute e limiter."""
    return await _get("/api/master")


@mcp.tool(annotations=IDEMPOTENT)
async def set_master(
    volume: Annotated[Optional[float], Field(ge=0, le=100, description="Volume master 0-100 (%)")] = None,
    muted: Optional[bool] = None,
    limiter_enabled: Optional[bool] = None,
    limiter_threshold_db: Annotated[Optional[float], Field(ge=-12, le=0, description="Teto do limiter em dB (-12 a 0)")] = None,
    compressor_enabled: Annotated[Optional[bool], Field(description="Compressor do master (une bumbo/caixa)")] = None,
    compressor_threshold_db: Annotated[Optional[float], Field(ge=-30, le=0, description="Limiar do compressor em dB")] = None,
    compressor_ratio: Annotated[Optional[float], Field(ge=1, le=10, description="Razão do compressor (2 = 2:1, suave)")] = None,
    compressor_makeup_db: Annotated[Optional[float], Field(ge=0, le=12, description="Ganho de compensação do compressor em dB")] = None,
) -> dict:
    """Ajusta o master (volume, mute, limiter e compressor). Só os campos informados mudam."""
    return await _post("/api/master", _drop_none({
        "volume": volume, "muted": muted,
        "limiter_enabled": limiter_enabled, "limiter_threshold_db": limiter_threshold_db,
        "compressor_enabled": compressor_enabled, "compressor_threshold_db": compressor_threshold_db,
        "compressor_ratio": compressor_ratio, "compressor_makeup_db": compressor_makeup_db,
    }))


# ── Configurações ────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def get_settings() -> dict:
    """Configurações gerais (sustain, sensibilidade a velocity, looper, kit browse...)."""
    return await _get("/api/settings")


@mcp.tool(annotations=IDEMPOTENT)
async def update_settings(
    sustain_mode: Annotated[Optional[bool], Field(description="Sample toca até o fim mesmo soltando o pad")] = None,
    velocity_sensitive: Annotated[Optional[bool], Field(description="Volume do pad segue a força da batida")] = None,
    auto_trim: Annotated[Optional[bool], Field(description="Pular o silêncio no início/fim de cada sample (padrão: ligado)")] = None,
    kit_browse_preview: Annotated[Optional[bool], Field(description="Tocar prévia ao navegar kits no controlador")] = None,
    looper_quantize: Annotated[Optional[bool], Field(description="Quantizar gravação do looper por compasso")] = None,
    looper_duplicate_hit_window_ms: Annotated[Optional[int], Field(ge=0, le=200, description="Janela (ms) para descartar batidas duplicadas no looper")] = None,
) -> dict:
    """Altera configurações gerais. Só os campos informados mudam."""
    body = _drop_none({"sustain_mode": sustain_mode, "velocity_sensitive": velocity_sensitive, "auto_trim": auto_trim})
    if body:
        await _post("/api/settings", body)
    if kit_browse_preview is not None:
        await _post("/api/settings/kit_browse_preview", {"enabled": kit_browse_preview})
    if looper_quantize is not None:
        await _post("/api/settings/looper_quantize", {"enabled": looper_quantize})
    if looper_duplicate_hit_window_ms is not None:
        await _post("/api/settings/looper_duplicate_hit_window", {"milliseconds": looper_duplicate_hit_window_ms})
    return await _get("/api/settings")


# ── Pads ─────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def list_pads() -> list[dict]:
    """Os 16 pads: sample atribuído, nota MIDI, volume (dB), pan e filtro."""
    return await _get("/api/pads")


@mcp.tool(annotations=IDEMPOTENT)
async def assign_pad_sound(
    pad_number: PadNumber,
    sample_id: Annotated[Optional[int], Field(description="ID do sample (list_sounds); null esvazia o pad")],
) -> dict:
    """Atribui um sample da biblioteca a um pad (ou esvazia com null)."""
    return await _post(f"/api/pads/{pad_number}/assign", {"sample_id": sample_id})


@mcp.tool(annotations=DESTRUCTIVE)
async def clear_all_pads() -> dict:
    """Remove o som dos 16 pads (mix, efeitos e knobs são mantidos)."""
    return await _post("/api/pads/clear")


@mcp.tool(annotations=IDEMPOTENT)
async def set_pad_mix(
    pad_number: PadNumber,
    volume_db: Annotated[Optional[float], Field(ge=-24, le=12, description="Volume em dB (-24 a +12; 0 = original)")] = None,
    pan: Annotated[Optional[float], Field(ge=-100, le=100, description="Pan: -100 esquerda, 0 centro, 100 direita")] = None,
    tone: Annotated[Optional[float], Field(ge=0, le=100, description="Filtro passa-baixa 0-100; 100 = sem filtro")] = None,
    tune: Annotated[Optional[float], Field(ge=-12, le=12, description="Afinação em semitons (-12 a +12; ex.: -2 a -4 deixa a caixa mais grave e encorpada)")] = None,
) -> dict:
    """Ajusta volume, pan, tom (filtro) e/ou afinação de um pad. Só os campos informados mudam."""
    return await _post(f"/api/pads/{pad_number}/mix", _drop_none({"volume_db": volume_db, "pan": pan, "tone": tone, "tune": tune}))


@mcp.tool(annotations=IDEMPOTENT)
async def set_pad_note(
    pad_number: PadNumber,
    midi_note: Annotated[int, Field(ge=0, le=127, description="Nota MIDI que dispara o pad")],
) -> dict:
    """Define a nota MIDI do controlador que dispara o pad."""
    return await _post(f"/api/pads/{pad_number}/note", {"midi_note": midi_note})


@mcp.tool(annotations=WRITE)
async def pad_note_learn(pad_number: PadNumber, cancel: bool = False) -> dict:
    """Inicia (ou cancela) o MIDI learn da nota do pad: o próximo pad batido no
    controlador físico passa a disparar este pad. Precisa de alguém no hardware."""
    suffix = "/cancel" if cancel else ""
    return await _post(f"/api/pads/{pad_number}/note/learn{suffix}")


@mcp.tool(annotations=WRITE)
async def trigger_pad(pad_number: PadNumber) -> dict:
    """Toca o pad uma vez (velocity 100), como um toque na tela. Sai no PA!"""
    return await _post(f"/api/pads/{pad_number}/trigger")


# ── Efeitos ──────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def list_effects_catalog() -> list[dict]:
    """Plugins LV2 disponíveis (plugin_id) com seus parâmetros, faixas e padrões."""
    return await _get("/api/effects/catalog")


@mcp.tool(annotations=READ)
async def list_pad_effects() -> list[dict]:
    """Efeitos atualmente carregados em cada pad/slot e seus valores."""
    return await _get("/api/pad-effects")


@mcp.tool(annotations=IDEMPOTENT)
async def set_pad_effect(
    pad_number: PadNumber,
    slot_index: EffectSlot,
    plugin_id: Annotated[Optional[str], Field(description="plugin_id do catálogo; null remove o efeito")],
) -> dict:
    """Coloca (ou remove) um efeito num slot do pad. Parâmetros voltam ao padrão
    e knobs MIDI ligados a esse slot são desfeitos."""
    return await _post(f"/api/pads/{pad_number}/effects/{slot_index}", {"plugin_id": plugin_id})


@mcp.tool(annotations=IDEMPOTENT)
async def set_pad_effect_param(
    pad_number: PadNumber,
    slot_index: EffectSlot,
    symbol: Annotated[str, Field(description="symbol do parâmetro (ver list_effects_catalog)")],
    value: float,
) -> dict:
    """Ajusta um parâmetro do efeito de um slot (dentro da faixa do catálogo)."""
    return await _post(f"/api/pads/{pad_number}/effects/{slot_index}/param", {"symbol": symbol, "value": value})


# ── Efeitos compartilhados (hall / plate / delay) e envios ─────────────────


@mcp.tool(annotations=READ)
async def get_fx_buses() -> dict:
    """Efeitos compartilhados por todos os pads: Reverb Hall, Reverb Plate e
    Delay (que segue o BPM). Traz o catálogo de parâmetros, os valores atuais e
    o envio (0-100%) de cada pad para cada um. Prefira estes efeitos para dar
    ambiência: são leves, ficam sempre carregados e a troca de cena só muda
    números. Os slots por pad (set_pad_effect) são para efeitos de caráter
    (pitch, EQ, drive) - poucos por cena."""
    return await _get("/api/fx-buses")


@mcp.tool(annotations=IDEMPOTENT)
async def set_pad_send(
    pad_number: PadNumber,
    bus: Literal["hall", "plate", "delay"],
    level: Annotated[float, Field(ge=0, le=100, description="Quanto o pad envia (%): 0 = nada, 70 = -6 dB, 100 = 0 dB")],
) -> dict:
    """Quanto um pad envia para um efeito compartilhado."""
    return await _post(f"/api/pads/{pad_number}/sends/{bus}", {"level": level})


@mcp.tool(annotations=IDEMPOTENT)
async def set_fx_bus_param(
    bus: Literal["hall", "plate", "delay"],
    symbol: Annotated[str, Field(description="symbol do parâmetro (ver get_fx_buses). No delay, 'beats' é a divisão: 1.5, 1, 0.6667, 0.75 (colcheia pontuada), 0.5, 0.375, 0.25")],
    value: float,
) -> dict:
    """Ajusta um parâmetro de um efeito compartilhado (vale para todos os pads)."""
    return await _post(f"/api/fx-buses/{bus}/param", {"symbol": symbol, "value": value})


# ── Biblioteca de sons ───────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def list_sounds(
    folder: Annotated[Optional[str], Field(description="Pasta para navegar ('' = raiz). Omitido: lista todos os sons")] = None,
    search: Annotated[Optional[str], Field(description="Filtra pelo nome (sem diferenciar maiúsculas)")] = None,
) -> dict:
    """Sons (samples) da biblioteca: id, nome, pasta. Com `folder`, devolve
    a navegação daquela pasta (subpastas + sons)."""
    if folder is not None:
        return await _get("/api/sounds/browse", params={"folder": folder})
    sounds = await _get("/api/sounds")
    if search:
        needle = search.casefold()
        sounds = [s for s in sounds if needle in s["display_name"].casefold() or needle in (s.get("folder") or "").casefold()]
    return {"sounds": sounds}


@mcp.tool(annotations=FETCHES)
async def upload_sound(
    source: Source,
    folder: Annotated[str, Field(description="Pasta da biblioteca onde guardar ('' = raiz)")] = "",
    display_name: Annotated[Optional[str], Field(description="Nome do som (padrão: nome do arquivo)")] = None,
) -> dict:
    """Adiciona um arquivo de áudio (wav/mp3/ogg/flac/aiff) à biblioteca, a
    partir de um caminho local ou URL. Devolve o id do novo sample."""
    name, content = await _read_source(source)
    if display_name:
        name = _safe_filename(display_name) + Path(name).suffix.lower()
    return await _call("POST", "/api/sounds/upload", files={"file": (name, content)}, data={"folder": folder})


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_sound(sample_id: int) -> dict:
    """Apaga um sample da biblioteca e do disco (falha se estiver em algum pad)."""
    return await _delete(f"/api/sounds/{sample_id}")


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "sample"


# ── Kits ─────────────────────────────────────────────────────────────────────


class KitPad(BaseModel):
    pad_number: PadNumber
    sample_id: Annotated[Optional[int], Field(description="ID do sample na biblioteca")] = None
    display_name: Annotated[Optional[str], Field(description="Nome no kit (padrão: nome do sample)")] = None


class KitFile(BaseModel):
    pad_number: PadNumber
    source: Source
    display_name: Annotated[Optional[str], Field(description="Nome do som (padrão: nome do arquivo)")] = None


@mcp.tool(annotations=READ)
async def list_kits(category: Optional[str] = None) -> list[dict]:
    """Kits do catálogo (com seus pads), opcionalmente filtrados por categoria."""
    kits = await _get("/api/kits")
    if category is not None:
        kits = [k for k in kits if k["category"] == category]
    return kits


@mcp.tool(annotations=READ)
async def list_kit_categories() -> list[str]:
    """Categorias de kit existentes."""
    return await _get("/api/kits/categories")


@mcp.tool(annotations=READ)
async def get_kit(kit_id: int) -> dict:
    """Um kit e seus pads."""
    return await _get(f"/api/kits/{kit_id}")


@mcp.tool(annotations=WRITE)
async def create_kit(
    name: str,
    pads: Annotated[list[KitPad], Field(description="Pads do kit (pad_number -> sample_id)")],
    category: str = "",
    sort_index: int = 0,
    overwrite: Annotated[bool, Field(description="Substituir um kit com o mesmo nome")] = False,
) -> dict:
    """Cria um kit com samples que já estão na biblioteca. Não altera os pads
    atuais; use load_kit para tocar o kit."""
    return await _post("/api/kits", {
        "name": name, "category": category, "sort_index": sort_index, "overwrite": overwrite,
        "pads": [p.model_dump() for p in pads],
    })


@mcp.tool(annotations=WRITE)
async def create_kit_from_pads(
    name: str,
    category: str = "",
    sort_index: int = 0,
    overwrite: Annotated[bool, Field(description="Substituir um kit com o mesmo nome")] = False,
) -> dict:
    """Salva os sons atualmente nos pads como um novo kit."""
    return await _post("/api/kits/from-pads", {
        "name": name, "category": category, "sort_index": sort_index, "overwrite": overwrite,
    })


@mcp.tool(annotations=FETCHES)
async def create_kit_from_files(
    name: str,
    files: Annotated[list[KitFile], Field(description="Arquivo de áudio (caminho local ou URL) para cada pad")],
    category: str = "",
    sort_index: int = 0,
) -> dict:
    """Cria um kit a partir de arquivos de áudio novos: importa cada arquivo
    para a biblioteca (pasta com o nome do kit) e monta o kit. Se já existir
    um kit com esse nome, ele é substituído."""
    if not files:
        raise ToolError("files não pode ser vazio")
    if len({f.pad_number for f in files}) != len(files):
        raise ToolError("pad_number repetido em files")
    buf = io.BytesIO()
    manifest_pads = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            filename, content = await _read_source(f.source)
            arcname = f"pad{f.pad_number:02d}{Path(filename).suffix.lower()}"
            zf.writestr(arcname, content)
            manifest_pads.append({
                "pad_number": f.pad_number,
                "filename": arcname,
                "display_name": f.display_name or Path(filename).stem,
            })
        manifest = {"kits": [{"name": name, "category": category, "sort_index": sort_index, "pads": manifest_pads}]}
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    result = await _call("POST", "/api/kits/import", files={"file": ("kit.zip", buf.getvalue(), "application/zip")})
    kit_ids = result.get("kit_ids", [])
    return {"ok": True, "kit": await _get(f"/api/kits/{kit_ids[0]}")} if kit_ids else result


@mcp.tool(annotations=FETCHES)
async def import_kit_pack(source: Annotated[str, Field(description="Caminho local ou URL de um pack .zip (manifest.json + áudios)")]) -> dict:
    """Importa um pack de kits .zip (formato do curate_kit_pack.py)."""
    name, content = await _read_source(source, allowed_ext={".zip"})
    return await _call("POST", "/api/kits/import", files={"file": (name, content, "application/zip")})


@mcp.tool(annotations=WRITE)
async def load_kit(
    kit_id: int,
    clear_other_pads: Annotated[bool, Field(description="Esvaziar os pads que o kit não usa")] = False,
) -> dict:
    """Carrega um kit nos pads: cada som do kit vai para o pad de mesmo número.
    Mix/efeitos dos pads são mantidos."""
    return await _post(f"/api/kits/{kit_id}/load", {"clear_other_pads": clear_other_pads})


@mcp.tool(annotations=READ)
async def list_hydrogen_kits() -> list[dict]:
    """Kits do Hydrogen instalados no computador do DiakoPad (nome, autor,
    licença, nº de instrumentos), prontos para importar."""
    return await _get("/api/hydrogen-kits")


@mcp.tool(annotations=WRITE)
async def import_hydrogen_kit(
    name: Annotated[str, Field(description="name de list_hydrogen_kits (pasta do kit)")],
) -> dict:
    """Importa um kit do Hydrogen: copia a camada de toque forte de cada
    instrumento para a biblioteca e cria um kit DiakoPad (categoria
    05_hydrogen) no layout padrão de pads. Reimportar atualiza o kit."""
    return await _post("/api/hydrogen-kits/import", {"name": name})


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_kit(kit_id: int) -> dict:
    """Apaga um kit do catálogo (os samples continuam na biblioteca)."""
    return await _delete(f"/api/kits/{kit_id}")


@mcp.tool(annotations=WRITE)
async def kit_browse(
    action: Literal["toggle", "select_target", "pick_candidate", "nav", "confirm", "back"],
    pad_number: Annotated[Optional[int], Field(ge=1, le=16, description="Para select_target (pad a trocar) e pick_candidate (som do kit)")] = None,
    direction: Optional[Literal["up", "down", "left", "right"]] = None,
) -> dict:
    """Modo de navegação de kits do controlador (troca o som de UM pad):
    toggle arma/sai; select_target escolhe o pad a trocar; nav up/down muda de
    kit e left/right de categoria; pick_candidate escolhe qual som do kit
    usar; confirm aplica; back cancela. Para trocar o kit inteiro, use load_kit."""
    if action in ("select_target", "pick_candidate") and pad_number is None:
        raise ToolError(f"{action} exige pad_number")
    if action == "nav" and direction is None:
        raise ToolError("nav exige direction")
    if action == "toggle":
        return await _post("/api/kit-browse/toggle")
    if action == "select_target":
        return await _post("/api/kit-browse/select-target", {"pad_number": pad_number})
    if action == "pick_candidate":
        return await _post(f"/api/kit-browse/preview/{pad_number}")
    if action == "nav":
        return await _post("/api/kit-browse/nav", {"direction": direction})
    if action == "confirm":
        return await _post("/api/kit-browse/confirm")
    return await _post("/api/kit-browse/back")


# ── Cenas ────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def list_scenes() -> list[dict]:
    """Cenas salvas (id, nome, se está ativa no carrossel do show)."""
    return await _get("/api/scenes")


@mcp.tool(annotations=WRITE)
async def save_scene(name: str) -> dict:
    """Salva o estado atual (pads, mix, efeitos, knobs, BPM, metrônomo,
    sequencer) como cena. Um nome existente é sobrescrito."""
    return await _post("/api/scenes", {"name": name})


@mcp.tool(annotations=WRITE)
async def load_scene(scene_id: int) -> dict:
    """Carrega uma cena: substitui pads, mix, efeitos, knobs, BPM e sequencer
    atuais (mudanças não salvas se perdem)."""
    return await _post(f"/api/scenes/{scene_id}/load")


@mcp.tool(annotations=IDEMPOTENT)
async def set_scene_active(scene_id: int, active: bool) -> dict:
    """Inclui/exclui a cena da navegação de cenas do controlador."""
    return await _post(f"/api/scenes/{scene_id}/active", {"active": active})


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_scene(scene_id: int) -> dict:
    """Apaga uma cena."""
    return await _delete(f"/api/scenes/{scene_id}")


# ── Tempo e metrônomo ────────────────────────────────────────────────────────


@mcp.tool(annotations=IDEMPOTENT)
async def set_tempo(bpm: Annotated[float, Field(ge=40, le=240, description="BPM global (40-240)")]) -> dict:
    """Define o BPM global (sequencer, metrônomo e looper)."""
    return await _post("/api/tempo", {"bpm": bpm})


@mcp.tool(annotations=READ)
async def get_metronome() -> dict:
    """Estado do metrônomo + estilos de clique e compassos disponíveis."""
    return {
        **(await _get("/api/metronome")),
        "bpm": (await _get("/api/tempo"))["bpm"],
        "available_styles": await _get("/api/metronome/styles"),
        "available_signatures": await _get("/api/metronome/time-signatures"),
    }


@mcp.tool(annotations=IDEMPOTENT)
async def set_metronome(
    running: Optional[bool] = None,
    style: Annotated[Optional[str], Field(description="Estilo de clique (ver get_metronome)")] = None,
    signature: Annotated[Optional[str], Field(description="Compasso, ex. '4/4' (ver get_metronome)")] = None,
) -> dict:
    """Liga/desliga o metrônomo e/ou muda estilo e compasso."""
    if style is not None:
        await _post("/api/metronome/style", {"style": style})
    if signature is not None:
        await _post("/api/metronome/signature", {"signature": signature})
    if running is not None:
        await _post("/api/metronome/transport", {"running": running})
    return await _get("/api/metronome")


# ── Sequencer ────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def get_sequencer() -> dict:
    """Estado do sequencer de 16 passos (rodando, passo atual, passos ativos)."""
    return await _get("/api/sequencer")


@mcp.tool(annotations=IDEMPOTENT)
async def set_sequencer_running(running: bool) -> dict:
    """Dá play/stop no sequencer."""
    return await _post("/api/sequencer/transport", {"running": running})


@mcp.tool(annotations=IDEMPOTENT)
async def set_sequencer_step(pad_number: PadNumber, step_index: StepIndex, active: bool) -> dict:
    """Liga/desliga um passo de um pad no sequencer."""
    return await _post(f"/api/sequencer/steps/{pad_number}/{step_index}", {"active": active})


@mcp.tool(annotations=IDEMPOTENT)
async def set_sequencer_pattern(
    steps: Annotated[dict[str, list[int]], Field(description='Pad -> passos ativos (0-15), ex. {"1": [0,4,8,12], "2": [4,12]}')],
    clear_first: Annotated[bool, Field(description="Limpar o sequencer antes (senão só liga os passos informados)")] = True,
) -> dict:
    """Programa um padrão inteiro de uma vez."""
    parsed: list[tuple[int, int]] = []
    for pad_key, indices in steps.items():
        try:
            pad = int(pad_key)
        except ValueError as exc:
            raise ToolError(f"pad inválido: {pad_key!r}") from exc
        if not 1 <= pad <= 16:
            raise ToolError(f"pad fora de 1-16: {pad}")
        for i in indices:
            if not 0 <= i <= 15:
                raise ToolError(f"passo fora de 0-15: {i} (pad {pad})")
            parsed.append((pad, i))
    if clear_first:
        await _post("/api/sequencer/clear")
    for pad, i in parsed:
        await _post(f"/api/sequencer/steps/{pad}/{i}", {"active": True})
    return await _get("/api/sequencer")


@mcp.tool(annotations=DESTRUCTIVE)
async def clear_sequencer() -> dict:
    """Desliga todos os passos do sequencer."""
    return await _post("/api/sequencer/clear")


@mcp.tool(annotations=READ)
async def list_patterns() -> list[dict]:
    """Padrões de sequencer salvos."""
    return await _get("/api/patterns")


@mcp.tool(annotations=WRITE)
async def save_pattern(name: str) -> dict:
    """Salva os passos atuais do sequencer como padrão."""
    return await _post("/api/patterns", {"name": name})


@mcp.tool(annotations=WRITE)
async def load_pattern(pattern_id: int) -> dict:
    """Carrega um padrão salvo no sequencer (substitui os passos atuais)."""
    return await _post(f"/api/patterns/{pattern_id}/load")


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_pattern(pattern_id: int) -> dict:
    """Apaga um padrão salvo."""
    return await _delete(f"/api/patterns/{pattern_id}")


# ── Looper ───────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def get_looper() -> dict:
    """Estado do looper MIDI de 4 trilhas."""
    return await _get("/api/looper")


@mcp.tool(annotations=WRITE)
async def looper_transport(
    action: Literal["record_start", "record_stop", "play", "stop", "overdub_start", "overdub_stop", "clear"],
) -> dict:
    """Controla o looper (trilha selecionada). `clear` apaga todas as trilhas."""
    path = {
        "record_start": "/api/looper/record/start",
        "record_stop": "/api/looper/record/stop",
        "play": "/api/looper/play",
        "stop": "/api/looper/stop",
        "overdub_start": "/api/looper/overdub/start",
        "overdub_stop": "/api/looper/overdub/stop",
        "clear": "/api/looper/clear",
    }[action]
    await _post(path)
    return await _get("/api/looper")


@mcp.tool(annotations=WRITE)
async def looper_track(
    track_index: TrackIndex,
    action: Literal["select", "toggle_mute", "set_volume", "clear"],
    volume: Annotated[Optional[float], Field(ge=0, le=100, description="Para set_volume: 0-100")] = None,
) -> dict:
    """Ações numa trilha do looper: selecionar, alternar mute, volume ou apagar."""
    if action == "set_volume":
        if volume is None:
            raise ToolError("set_volume exige volume")
        await _post(f"/api/looper/tracks/{track_index}/volume", {"volume": volume})
    else:
        segment = {"select": "select", "toggle_mute": "mute", "clear": "clear"}[action]
        await _post(f"/api/looper/tracks/{track_index}/{segment}")
    return await _get("/api/looper")


# ── Knobs (MIDI CC) e ações do controlador ───────────────────────────────────


@mcp.tool(annotations=READ)
async def list_knob_mappings() -> dict:
    """Knobs (CC MIDI) mapeados e o learn pendente."""
    return await _get("/api/knobs")


@mcp.tool(annotations=READ)
async def list_knob_targets() -> dict:
    """Parâmetros que podem ser ligados a um knob (globais e por pad)."""
    return await _get("/api/knobs/targets")


@mcp.tool(annotations=WRITE)
async def knob_learn(
    scope: Optional[Literal["pad", "global"]] = None,
    param: Annotated[Optional[str], Field(description="param de list_knob_targets")] = None,
    pad_number: Annotated[Optional[int], Field(ge=1, le=16)] = None,
    cancel: bool = False,
) -> dict:
    """Inicia o MIDI learn de um knob: o próximo knob girado no controlador
    passa a controlar `param`. cancel=True cancela. Precisa de alguém no hardware."""
    if cancel:
        return await _post("/api/knobs/learn/cancel")
    if scope is None or param is None:
        raise ToolError("informe scope e param (ou cancel=True)")
    return await _post("/api/knobs/learn", {"scope": scope, "param": param, "pad_number": pad_number})


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_knob_mapping(
    cc_number: Annotated[Optional[int], Field(ge=0, le=127, description="CC a desmapear")] = None,
    all_mappings: Annotated[bool, Field(description="True apaga TODOS os mapeamentos de knob")] = False,
) -> dict:
    """Remove o mapeamento de um knob (cc_number) ou de todos (all_mappings=True)."""
    if all_mappings:
        return await _delete("/api/knobs")
    if cc_number is None:
        raise ToolError("informe cc_number ou all_mappings=True")
    return await _delete(f"/api/knobs/{cc_number}")


@mcp.tool(annotations=READ)
async def list_controller_actions() -> dict:
    """Botões do controlador ligados a ações (cena seguinte, looper, panic...)."""
    return await _get("/api/controller-actions")


@mcp.tool(annotations=WRITE)
async def controller_action_learn(
    action: Annotated[Optional[str], Field(description="Ex.: scene_next, scene_prev, looper_record_toggle, panic, tap_tempo, kit_browse_toggle...")] = None,
    cancel: bool = False,
) -> dict:
    """Inicia o learn de uma ação: o próximo botão apertado no controlador
    passa a disparar a ação. Precisa de alguém no hardware."""
    if cancel:
        return await _post("/api/controller-actions/learn/cancel")
    if not action:
        raise ToolError("informe action (ou cancel=True)")
    return await _post(f"/api/controller-actions/{action}/learn")


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_controller_binding(binding_id: int) -> dict:
    """Remove a ligação de um botão a uma ação."""
    return await _delete(f"/api/controller-actions/bindings/{binding_id}")


# ── SMC-PAD: configuração do controlador (pads, botões, knobs, globais, calibração) ──
# A memória do aparelho é lida/gravada pela aba "SMCPAD Config" do DiakoPad. Gravar é opt-in:
# o USUÁRIO liga "Gravação" nessa aba; sem isso o servidor recusa as edições (403) e o agente
# não consegue ligar sozinho. Cada gravação faz backup, confere a leitura e só então grava de vez.

SmcPadSection = Literal["summary", "pads", "buttons", "knobs", "globals", "calibration", "all"]


class SmcPadEdit(BaseModel):
    """Uma alteração de campo, com o valor como aparece na tela do Midi Suite."""

    scope: Literal["pad", "button", "knob", "globals", "calibration"]
    field: str = Field(
        description=(
            "pad: type, channel(1-16), note, min_vel, max_vel, color(#RRGGBB), led(0-255) · "
            "button: mode, channel, cc, value1, value2, led, sysex('F0 7F ... F7', até 16 bytes) · "
            "knob: type, speed, channel, cc, min, max · "
            "globals: tempo, swing, time, sync, latch, octave, transpose, pad_curve, aftertouch, preset · "
            "calibration: level(1-8) ou raw(50-750). Os valores de type/mode/speed/time são o ÍNDICE da lista "
            "em smcpad_get_config(section='summary').enums."
        )
    )
    value: int | str
    preset: Optional[int] = Field(default=None, ge=1, le=8, description="1-8; obrigatório para pad/button/knob. Só o preset ATIVO no aparelho pode ser editado")
    index: Optional[int] = Field(default=None, ge=0, le=127, description="pad: 0-127 = (grupo-1)*16 + (nº do pad-1); button: 0-4; knob: 0-15 (0-7 banco 1, 8-15 banco 2)")
    pad: Optional[int] = Field(default=None, ge=1, le=16, description="só para calibration: nº do pad (1-16)")
    expected: Optional[int | str] = Field(default=None, description="valor atual esperado; se o aparelho tiver outro, a edição é recusada (evita sobrescrever mudança alheia)")


async def _get_bytes(path: str) -> bytes:
    try:
        res = await _http().get(path)
    except httpx.HTTPError as exc:
        raise ToolError(f"DiakoPad inacessível em {BASE_URL}: {exc}") from exc
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", res.text)
        except ValueError:
            detail = res.text
        raise ToolError(f"DiakoPad respondeu {res.status_code}: {detail}")
    return res.content


def _compact_pad(pad: dict) -> dict:
    out = {k: v for k, v in pad.items() if k != "extra"}
    if any(c != "0" for c in pad.get("extra", "")):
        out["extra"] = pad["extra"]
    return out


@mcp.tool(annotations=READ)
async def smcpad_status() -> dict:
    """SMC-PAD conectado? Modelo, globais atuais (preset ativo, banco de pads A/B) e se a
    gravação no aparelho está ligada. Sem aparelho retorna connected=false e o motivo."""
    return await _get("/api/smcpad/status")


@mcp.tool(annotations=READ)
async def smcpad_get_config(
    preset: Annotated[Optional[int], Field(ge=1, le=8, description="Preset 1-8 (padrão: o ativo no aparelho)")] = None,
    section: Annotated[SmcPadSection, Field(description="summary (globais, calibração, listas de opções e limites), pads, buttons, knobs, globals, calibration ou all")] = "summary",
    group: Annotated[Optional[int], Field(ge=1, le=8, description="Só para section=pads: grupo de 16 pads (grupo 3 = notas 36-51, o banco A de fábrica; grupo 4 = 52-67, banco B)")] = None,
) -> dict:
    """Lê a configuração do SMC-PAD direto do aparelho. Cada preset tem 128 pads (8 grupos de 16),
    5 botões, 16 knobs; há ainda os globais e a calibração (16 pads). Use section para não trazer tudo."""
    res = await _get("/api/smcpad/config", params={"preset": preset} if preset else None)
    cfg = res["preset"]
    head = {k: res[k] for k in ("identity", "write_enabled", "current_preset", "preset_number")}
    globals_ = {k: v for k, v in res["globals"].items() if k != "raw"}
    if section == "pads":
        pads = cfg["pads"] if group is None else [p for p in cfg["pads"] if p["group"] == group]
        return {**head, "pads": [_compact_pad(p) for p in pads]}
    if section == "buttons":
        return {**head, "buttons": cfg["buttons"]}
    if section == "knobs":
        return {**head, "knobs": cfg["knobs"]}
    if section == "globals":
        return {**head, "globals": globals_}
    if section == "calibration":
        return {**head, "calibration": res["calibration"]}
    if section == "all":
        return {**head, "globals": globals_, "calibration": res["calibration"], "buttons": cfg["buttons"], "knobs": cfg["knobs"], "pads": [_compact_pad(p) for p in cfg["pads"]], "enums": res["enums"], "limits": res["limits"]}
    return {**head, "globals": globals_, "calibration": res["calibration"], "enums": res["enums"], "limits": res["limits"]}


@mcp.tool(annotations=DESTRUCTIVE)
async def smcpad_edit(
    edits: Annotated[list[SmcPadEdit], Field(min_length=1, max_length=64, description="Alterações a gravar; vão juntas num único backup")],
    commit: Annotated[bool, Field(description="Gravar de vez no aparelho (padrão). False deixa só ativo até desligar")] = True,
) -> dict:
    """Grava alterações no SMC-PAD: valida tudo antes, faz backup do aparelho inteiro, escreve, lê de
    volta para conferir (se divergir, restaura os bytes originais e não grava de vez) e dá o commit.
    Exige que o usuário tenha ligado "Gravação" na aba SMCPAD Config (senão responde 403 — peça a ele; não há
    como ligar por aqui). Pads/botões/knobs só valem no preset ativo; para outro preset, edite
    scope=globals field=preset primeiro (na mesma chamada). Evite durante o show."""
    return await _post("/api/smcpad/edits", {"edits": [e.model_dump(exclude_none=True) for e in edits], "commit": commit})


@mcp.tool(annotations=READ)
async def smcpad_list_backups() -> list[dict]:
    """Backups do aparelho feitos antes de cada gravação (arquivos .bin de 28.312 bytes em
    backend/smcpad_backups/ no DiakoPad)."""
    return await _get("/api/smcpad/backups")


@mcp.tool(annotations=WRITE)
async def smcpad_export_preset(
    preset: Annotated[int, Field(ge=1, le=8)],
    destination: Annotated[str, Field(description="Caminho local (máquina do MCP) do arquivo .spc a criar")],
) -> dict:
    """Salva um preset do aparelho num arquivo .spc (3.539 bytes, o mesmo formato do Midi Suite)."""
    path = Path(destination).expanduser()
    if path.suffix.lower() != ".spc":
        raise ToolError("o arquivo deve terminar em .spc")
    data = await _get_bytes(f"/api/smcpad/preset/{preset}/export")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "bytes": len(data), "preset": preset}


@mcp.tool(annotations=DESTRUCTIVE)
async def smcpad_import_preset(
    preset: Annotated[int, Field(ge=1, le=8, description="Preset de destino; precisa ser o ativo no aparelho")],
    source: Annotated[str, Field(description="Caminho local (máquina do MCP) de um arquivo .spc de 3.539 bytes")],
) -> dict:
    """Grava um arquivo .spc no preset ativo do aparelho, SOBRESCREVENDO-o (com backup, conferência e commit).
    Exige "Gravação" ligada pelo usuário na aba SMCPAD Config."""
    path = Path(source).expanduser()
    if path.suffix.lower() not in (".spc", ".bin") or not path.is_file():
        raise ToolError(f"arquivo .spc não encontrado: {path}")
    return await _call("POST", f"/api/smcpad/preset/{preset}/import", content=path.read_bytes(), headers={"Content-Type": "application/octet-stream"})


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()

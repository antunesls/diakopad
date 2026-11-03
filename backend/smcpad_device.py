"""Acesso ao SMC-PAD pela porta MIDI: leitura, escrita segura e backup.

Regras de segurança (a gravação mexe na memória do aparelho):
- nada é gravado se `write_enabled()` for falso (padrão do DiakoPad);
- antes de qualquer escrita lê o aparelho inteiro e guarda um backup em disco;
- depois de escrever lê de volta e compara; se divergir, tenta restaurar os bytes
  originais e NÃO grava de vez (commit) — o aparelho volta ao estado anterior;
- edições de pad/botão/knob só valem para o preset atual do aparelho (o mesmo
  fluxo do Midi Suite: primeiro ativa o preset, depois edita).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

import smcpad as sp

logger = logging.getLogger("diakopad.smcpad")

DEFAULT_PORT_PATTERN = r"SINCO|SMC-PAD"


class DeviceError(RuntimeError):
    """Falha de comunicação com o aparelho (sem porta, sem resposta, resposta inválida)."""


class WriteDisabled(DeviceError):
    """A gravação no aparelho está desligada nas configurações."""


class VerifyError(DeviceError):
    """A leitura de conferência não bateu com o que foi gravado."""


class Transport(Protocol):
    def exchange(self, sysex: bytes, timeout: float = 1.0) -> Optional[bytes]:
        """Envia uma mensagem SysEx e devolve a próxima SysEx recebida (ou None)."""

    def close(self) -> None: ...


def choose_port(names: list[str], pattern: str = DEFAULT_PORT_PATTERN) -> Optional[str]:
    """Escolhe a porta de configuração do SMC-PAD entre as portas MIDI do sistema.

    O aparelho expõe 3 portas (no Linux: "SMC-PAD-Private", "SMC-PAD-Master" e uma sem
    nome). A configuração responde na "Private" (cabo 0); no Windows ela aparece só como
    "SMC-PAD". Sem "Private", vale a primeira na ordem do sistema (não a ordem alfabética,
    que preferiria a porta sem nome)."""
    regex = re.compile(pattern)
    matches = [n for n in names if regex.search(n) and "Midi Through" not in n]
    private = [n for n in matches if "private" in n.lower()]
    chosen = private or matches
    return chosen[0] if chosen else None


class MidoTransport:
    """Porta MIDI do aparelho via mido/rtmidi (entrada e saída com o mesmo nome base)."""

    def __init__(self, pattern: str = DEFAULT_PORT_PATTERN) -> None:
        import mido

        self._mido = mido
        out_name = choose_port(mido.get_output_names(), pattern)
        in_name = choose_port(mido.get_input_names(), pattern)
        if not out_name or not in_name:
            raise DeviceError("SMC-PAD não encontrado nas portas MIDI (está ligado e conectado?)")
        self._out = mido.open_output(out_name)
        self._in = mido.open_input(in_name)

    def exchange(self, sysex: bytes, timeout: float = 1.0) -> Optional[bytes]:
        for _ in self._in.iter_pending():  # descarta o que sobrou de antes
            pass
        self._out.send(self._mido.Message("sysex", data=list(sysex[1:-1])))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self._in.iter_pending():
                if msg.type == "sysex":
                    return b"\xf0" + bytes(msg.data) + b"\xf7"
            time.sleep(0.002)
        return None

    def close(self) -> None:
        for port in (self._in, self._out):
            try:
                port.close()
            except Exception:  # pragma: no cover - depende do backend
                pass


class SmcPadDevice:
    """Operações de protocolo sobre um Transport (sem política de segurança)."""

    def __init__(self, transport: Transport, *, timeout: float = 1.0, retries: int = 2) -> None:
        self._t = transport
        self._timeout = timeout
        self._retries = retries

    def _call(self, frame: bytes) -> bytes:
        message = sp.to_sysex(frame)
        for attempt in range(self._retries + 1):
            reply = self._t.exchange(message, self._timeout)
            if reply is not None:
                try:
                    return sp.from_sysex(reply)
                except Exception as exc:  # pragma: no cover - defensivo
                    raise DeviceError(f"resposta ilegível: {exc}") from exc
            logger.debug("sem resposta (tentativa %d)", attempt + 1)
        raise DeviceError("o SMC-PAD não respondeu")

    def identify(self) -> str:
        try:
            return sp.parse_identify(self._call(sp.identify_request()))
        except sp.ProtocolError as exc:
            raise DeviceError(str(exc)) from exc

    def read_region(self, region: int, address: int, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            n = min(sp.READ_CHUNK, length - len(out))
            frame = self._call(sp.read_request(region, address + len(out), n))
            try:
                out += sp.parse_read_response(frame, region, address + len(out), n)
            except sp.ProtocolError as exc:
                raise DeviceError(str(exc)) from exc
        return bytes(out)

    def write_region(self, region: int, address: int, data: bytes) -> None:
        done = 0
        while done < len(data):
            chunk = data[done : done + sp.WRITE_CHUNK]
            self._ack(self._call(sp.write_request(region, address + done, chunk)))
            done += len(chunk)
        if not data:
            self._ack(self._call(sp.write_request(region, address, b"")))

    def commit(self, region: int = sp.REGION_CONFIG) -> None:
        # A gravação em memória permanente demora (~0,25 s no aparelho real).
        old, self._timeout = self._timeout, max(self._timeout, 3.0)
        try:
            self._ack(self._call(sp.commit_request(region)))
        finally:
            self._timeout = old

    def _ack(self, frame: bytes) -> None:
        try:
            sp.parse_ack(frame)
        except sp.ProtocolError as exc:
            raise DeviceError(str(exc)) from exc

    def read_globals(self) -> bytes:
        return self.read_region(sp.REGION_GLOBALS, 0, sp.GLOBALS_SIZE)

    def read_calibration(self) -> bytes:
        return self.read_region(sp.REGION_CALIBRATION, 0, sp.CALIBRATION_SIZE)

    def read_config(self) -> bytes:
        return self.read_region(sp.REGION_CONFIG, 0, sp.CONFIG_SIZE)

    def close(self) -> None:
        self._t.close()


@dataclass
class Snapshot:
    identity: str
    globals_raw: bytes
    calibration: bytes
    config: bytes

    @property
    def globals(self) -> dict[str, Any]:
        return sp.parse_globals(self.globals_raw)

    @property
    def current_preset(self) -> int:
        return self.globals["preset"]


def _current_value(snapshot: Snapshot, edit: dict[str, Any]) -> Any:
    """Valor atual (como na tela) do campo que a edição vai mudar — para conferir e relatar."""
    scope, field = edit["scope"], edit["field"]
    if scope == "globals":
        return snapshot.globals[field]
    if scope == "calibration":
        pad = sp.parse_calibration(snapshot.calibration)[edit["pad"] - 1]
        return pad["level"] if field == "level" else pad["raw"]
    preset = sp.parse_preset(sp.preset_slice(snapshot.config, edit["preset"]))
    key = {"button": "buttons", "knob": "knobs", "pad": "pads"}[scope]
    return preset[key][edit["index"]][field]


class SmcPadService:
    """Política de segurança + cache, sobre um SmcPadDevice criado sob demanda."""

    def __init__(
        self,
        transport_factory: Callable[[], Transport],
        backup_dir: Path,
        write_enabled: Callable[[], bool],
    ) -> None:
        self._factory = transport_factory
        self._backup_dir = Path(backup_dir)
        self._write_enabled = write_enabled
        self._lock = threading.RLock()

    # -- acesso ---------------------------------------------------------------
    def _open(self) -> SmcPadDevice:
        try:
            return SmcPadDevice(self._factory())
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f"não foi possível abrir a porta MIDI do SMC-PAD: {exc}") from exc

    def write_enabled(self) -> bool:
        return bool(self._write_enabled())

    def status(self) -> dict[str, Any]:
        with self._lock:
            device = self._open()
            try:
                identity = device.identify()
                globals_ = sp.parse_globals(device.read_globals())
            finally:
                device.close()
        return {"connected": True, "identity": identity, "globals": globals_, "write_enabled": self.write_enabled()}

    def read_all(self, device: Optional[SmcPadDevice] = None) -> Snapshot:
        own = device is None
        with self._lock:
            device = device or self._open()
            try:
                return Snapshot(
                    identity=device.identify(),
                    globals_raw=device.read_globals(),
                    calibration=device.read_calibration(),
                    config=device.read_config(),
                )
            finally:
                if own:
                    device.close()

    def snapshot(self) -> Snapshot:
        return self.read_all()

    # -- backup ---------------------------------------------------------------
    def save_backup(self, snap: Snapshot, label: str = "antes-de-gravar") -> Path:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = self._backup_dir / f"smcpad-{stamp}-{label}"
        base.with_suffix(".bin").write_bytes(snap.config)  # compatível com o SPB.bin do Midi Suite
        base.with_suffix(".json").write_text(
            json.dumps(
                {
                    "identity": snap.identity,
                    "globals_hex": snap.globals_raw.hex(),
                    "calibration_hex": snap.calibration.hex(),
                    "config_file": base.with_suffix(".bin").name,
                },
                indent=2,
            )
        )
        return base.with_suffix(".bin")

    # -- escrita --------------------------------------------------------------
    def _require_write(self) -> None:
        if not self.write_enabled():
            raise WriteDisabled("gravação no aparelho desligada (ative em Config do SMCPAD)")

    def _plan(self, snap: Snapshot, edits: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int, int, bytes]]:
        if not edits:
            raise sp.EditError("nenhuma edição enviada")
        # A edição do preset atual vai primeiro: as demais valem para o preset que ela ativa.
        ordered = sorted(edits, key=lambda e: 0 if (e.get("scope"), e.get("field")) == ("globals", "preset") else 1)
        effective = snap.current_preset
        for e in ordered:
            if (e.get("scope"), e.get("field")) == ("globals", "preset") and isinstance(e.get("value"), int):
                effective = e["value"]
        plan = []
        for e in ordered:
            region, address, data = sp.plan_edit(e)
            if e["scope"] in ("button", "knob", "pad") and e["preset"] != effective:
                raise sp.EditError(f"ative o preset {e['preset']} no aparelho antes de editá-lo (atual: {effective})")
            if "expected" in e and _current_value(snap, e) != e["expected"]:
                raise sp.EditError("o valor atual no aparelho mudou desde que você abriu a tela; recarregue")
            plan.append((e, region, address, data))
        return plan

    def apply_edits(self, edits: list[dict[str, Any]], *, commit: bool = True) -> dict[str, Any]:
        self._require_write()
        with self._lock:
            device = self._open()
            try:
                snap = self.read_all(device)
                plan = self._plan(snap, edits)
                backup = self.save_backup(snap)
                before = {id(p[0]): _current_value(snap, p[0]) for p in plan}
                originals = [self._original(snap, region, address, len(data)) for _, region, address, data in plan]
                done: list[int] = []
                try:
                    for i, (_, region, address, data) in enumerate(plan):
                        device.write_region(region, address, data)
                        done.append(i)
                    for _, region, address, data in plan:
                        back = device.read_region(region, address, len(data))
                        if back != data:
                            raise VerifyError(f"conferência falhou em região {region}, endereço 0x{address:x}")
                except Exception:
                    self._rollback(device, plan, originals, done)
                    raise
                committed = False
                if commit:
                    regions = {region for _, region, _, _ in plan}
                    if sp.REGION_CALIBRATION in regions:
                        device.commit(sp.REGION_CALIBRATION)
                    if regions & {sp.REGION_CONFIG, sp.REGION_GLOBALS}:
                        device.commit(sp.REGION_CONFIG)
                    committed = True
                return {
                    "ok": True,
                    "committed": committed,
                    "backup": str(backup),
                    "applied": [{"edit": e, "before": before[id(e)], "after": e["value"]} for e, *_ in plan],
                }
            finally:
                device.close()

    # -- cores dos pads (usadas pelas cenas) ------------------------------------
    @staticmethod
    def _pad_area(preset: int) -> tuple[int, int]:
        """(endereço, tamanho) dos 128 registros de pad do preset (1..8) na região de configuração."""
        return (preset - 1) * sp.PRESET_SIZE + sp.PAD_BASE, sp.PAD_COUNT * sp.PAD_SIZE

    @staticmethod
    def _colors_of(block: bytes) -> list[str]:
        out = []
        for i in range(sp.PAD_COUNT):
            r, g, b = block[i * sp.PAD_SIZE + 5 : i * sp.PAD_SIZE + 8]
            out.append(f"#{r:02x}{g:02x}{b:02x}")
        return out

    def _open_fast(self) -> SmcPadDevice:
        """Para ganchos de cena: se o aparelho não responde, desiste rápido em vez de travar a troca de cena."""
        try:
            return SmcPadDevice(self._factory(), timeout=0.5, retries=0)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f"não foi possível abrir a porta MIDI do SMC-PAD: {exc}") from exc

    def read_pad_colors(self) -> list[str]:
        """As 128 cores dos pads do preset ATIVO no aparelho, como "#rrggbb"."""
        with self._lock:
            device = self._open_fast()
            try:
                preset = sp.parse_globals(device.read_globals())["preset"]
                address, length = self._pad_area(preset)
                return self._colors_of(device.read_region(sp.REGION_CONFIG, address, length))
            finally:
                device.close()

    def apply_pad_colors(self, colors: list[str]) -> dict[str, Any]:
        """Pinta os pads do preset ativo AO VIVO: só os bytes de cor que diferem, na memória de trabalho
        do aparelho. Sem backup e sem commit (nada vai para a memória permanente: ao desligar o aparelho
        volta às cores gravadas), por isso não depende de "Gravação" e é seguro numa troca de cena.
        Confere a leitura; se divergir, restaura os bytes originais."""
        if len(colors) != sp.PAD_COUNT:
            raise sp.EditError(f"são necessárias {sp.PAD_COUNT} cores")
        wanted = [sp._hex_color(c) for c in colors]
        with self._lock:
            device = self._open_fast()
            try:
                preset = sp.parse_globals(device.read_globals())["preset"]
                address, length = self._pad_area(preset)
                block = device.read_region(sp.REGION_CONFIG, address, length)
                new = bytearray(block)
                changed = []
                for i, rgb in enumerate(wanted):
                    off = i * sp.PAD_SIZE + 5
                    if new[off : off + 3] != rgb:
                        new[off : off + 3] = rgb
                        changed.append(off)
                if not changed:
                    return {"changed": 0, "committed": False}
                lo, hi = changed[0], changed[-1] + 3  # trecho contíguo que cobre todas as cores alteradas
                try:
                    device.write_region(sp.REGION_CONFIG, address + lo, bytes(new[lo:hi]))
                    if device.read_region(sp.REGION_CONFIG, address + lo, hi - lo) != bytes(new[lo:hi]):
                        raise VerifyError("conferência das cores falhou")
                except Exception:
                    try:
                        device.write_region(sp.REGION_CONFIG, address + lo, bytes(block[lo:hi]))
                    except Exception:  # pragma: no cover - melhor esforço
                        logger.exception("falha ao restaurar as cores")
                    raise
                return {"changed": len(changed), "committed": False}
            finally:
                device.close()

    @staticmethod
    def _original(snap: Snapshot, region: int, address: int, length: int) -> bytes:
        source = {sp.REGION_CONFIG: snap.config, sp.REGION_GLOBALS: snap.globals_raw, sp.REGION_CALIBRATION: snap.calibration}[region]
        return source[address : address + length]

    @staticmethod
    def _rollback(device: SmcPadDevice, plan, originals: list[bytes], done: list[int]) -> None:
        for i in reversed(done):
            _, region, address, _ = plan[i]
            try:
                device.write_region(region, address, originals[i])
            except Exception:  # pragma: no cover - melhor esforço
                logger.exception("falha ao restaurar região %d endereço 0x%x", region, address)

    def import_preset(self, preset: int, data: bytes, *, commit: bool = True) -> dict[str, Any]:
        """Grava um arquivo .spc (um preset de 3.539 bytes) no preset atual."""
        self._require_write()
        if len(data) != sp.PRESET_SIZE:
            raise sp.EditError(f"arquivo de preset deve ter {sp.PRESET_SIZE} bytes, veio com {len(data)}")
        with self._lock:
            device = self._open()
            try:
                snap = self.read_all(device)
                if preset != snap.current_preset:
                    raise sp.EditError(f"ative o preset {preset} no aparelho antes de importar (atual: {snap.current_preset})")
                backup = self.save_backup(snap, "antes-de-importar")
                address = (preset - 1) * sp.PRESET_SIZE
                original = snap.config[address : address + sp.PRESET_SIZE]
                try:
                    device.write_region(sp.REGION_CONFIG, address, data)
                    if device.read_region(sp.REGION_CONFIG, address, sp.PRESET_SIZE) != data:
                        raise VerifyError("conferência da importação falhou")
                except Exception:
                    try:
                        device.write_region(sp.REGION_CONFIG, address, original)
                    except Exception:  # pragma: no cover
                        logger.exception("falha ao restaurar o preset %d", preset)
                    raise
                if commit:
                    device.commit(sp.REGION_CONFIG)
                return {"ok": True, "committed": commit, "backup": str(backup)}
            finally:
                device.close()

    def export_preset(self, preset: int) -> bytes:
        return sp.preset_slice(self.snapshot().config, preset)

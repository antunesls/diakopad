"""Protocolo e modelo de dados do M-Vave SMC-PAD.

Tudo aqui é puro (sem MIDI, sem I/O): quadros SysEx, checksum, empacotamento em
7 bits e a decodificação/edição da memória de configuração do aparelho. O
protocolo foi obtido por engenharia reversa do Midi Suite e conferido contra o
aparelho real; veja docs/smcpad-protocolo.md para a evidência de cada campo.

Memória do aparelho:
- região 4 (12 bytes): globais (tempo, swing, preset atual, banco de pads...);
- região 5 (28.312 bytes): 8 presets de 3.539 bytes, um depois do outro.
"""
from __future__ import annotations

import re
from typing import Any, Optional

HEADER = b"\x00\x59"
CMD_ACK = 0x00
CMD_IDENTIFY = 0x11
CMD_WRITE = 0x22
CMD_READ = 0x23

REGION_CALIBRATION = 3
REGION_GLOBALS = 4
REGION_CONFIG = 5
CALIBRATION_SIZE = 160  # 16 pads x 10 bytes (5 valores u16; só o primeiro muda com o nível)
CALIBRATION_STRIDE = 10
CALIBRATION_PADS = 16
CALIBRATION_LEVELS = 8
GLOBALS_SIZE = 12
PRESET_COUNT = 8
PRESET_SIZE = 3539
CONFIG_SIZE = PRESET_COUNT * PRESET_SIZE  # 28312

READ_CHUNK = 1009  # o Midi Suite pede no máximo isto por leitura
WRITE_CHUNK = 1024  # e grava em blocos deste tamanho

# --- layout de um preset (offsets relativos ao início do preset) -------------
BUTTON_COUNT, BUTTON_SIZE, BUTTON_BASE = 5, 23, 0x00
KNOB_COUNT, KNOB_SIZE, KNOB_BASE = 16, 6, 0x73
PAD_COUNT, PAD_SIZE, PAD_BASE = 128, 26, 0xD3
PAD_GROUP_SIZE = 16
SYSEX_MAX = 16

BUTTON_NAMES = ["<", ">", "Play", "Pause", "Rec"]  # CC 25..29 de fábrica

BUTTON_MODES = ["CC Single", "CC Toggle", "CC Push", "Program", "Custom", "Note"]
PAD_TYPES = ["Note", "CC Toggle", "Momentary", "Program", "MCP", "Custom", "Comb MCP"]
KNOB_TYPES = ["CC", "Aftertouch", "Note", "CW"]
KNOB_SPEEDS = ["Lowest", "Low", "Normal", "Fast", "Fastest"]
TIME_VALUES = ["1/4", "1/4T", "1/8", "1/8T", "1/16", "1/16T", "1/32", "1/32T"]


class ProtocolError(ValueError):
    """Quadro malformado, checksum errado ou resposta que não bate com o pedido."""


# --- bytes -------------------------------------------------------------------

def checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def pack7(data: bytes) -> bytes:
    """Bytes de 8 bits -> grupos de 7 bits (primeiro bit menos significativo)."""
    out = bytearray()
    acc = 0
    bits = 0
    for byte in data:
        acc |= byte << bits
        bits += 8
        while bits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            bits -= 7
    if bits:
        out.append(acc & 0x7F)
    return bytes(out)


def unpack7(data: bytes) -> bytes:
    out = bytearray()
    acc = 0
    bits = 0
    for byte in data:
        acc |= (byte & 0x7F) << bits
        bits += 7
        if bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(out)


# --- quadros -----------------------------------------------------------------

def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    return HEADER + bytes([cmd]) + len(payload).to_bytes(3, "little") + payload + bytes([checksum(payload)])


def parse_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 7:
        raise ProtocolError(f"quadro curto demais ({len(frame)} bytes)")
    if frame[:2] != HEADER:
        raise ProtocolError(f"cabeçalho inesperado: {frame[:2].hex()}")
    length = int.from_bytes(frame[3:6], "little")
    if len(frame) != length + 7:
        raise ProtocolError(f"tamanho declarado {length} não bate com o quadro de {len(frame)} bytes")
    payload = frame[6 : 6 + length]
    if frame[6 + length] != checksum(payload):
        raise ProtocolError("checksum inválido")
    return frame[2], payload


def to_sysex(frame: bytes) -> bytes:
    """Quadro lógico -> mensagem SysEx completa (F0 ... F7)."""
    return b"\xf0" + pack7(frame) + b"\xf7"


def from_sysex(message: bytes) -> bytes:
    """Mensagem SysEx (com ou sem F0/F7) -> quadro lógico."""
    body = message
    if body[:1] == b"\xf0":
        body = body[1:]
    if body[-1:] == b"\xf7":
        body = body[:-1]
    return unpack7(body)


def identify_request() -> bytes:
    return build_frame(CMD_IDENTIFY)


def read_request(region: int, address: int, length: int) -> bytes:
    payload = bytes([region]) + address.to_bytes(4, "little") + length.to_bytes(3, "little")
    return build_frame(CMD_READ, payload)


def write_request(region: int, address: int, data: bytes) -> bytes:
    payload = bytes([region]) + address.to_bytes(4, "little") + len(data).to_bytes(3, "little") + data
    return build_frame(CMD_WRITE, payload)


def commit_request(region: int = REGION_CONFIG) -> bytes:
    """Escrita de tamanho 0 em endereço 0: grava a região no aparelho.

    Configuração e globais usam a região 5; a calibração tem o próprio commit (região 3)."""
    return write_request(region, 0, b"")


def parse_identify(frame: bytes) -> str:
    cmd, payload = parse_frame(frame)
    if cmd != CMD_IDENTIFY:
        raise ProtocolError(f"resposta de identificação com comando 0x{cmd:02x}")
    return payload.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def parse_read_response(frame: bytes, region: int, address: int, length: int) -> bytes:
    cmd, payload = parse_frame(frame)
    if cmd == CMD_ACK:
        raise ProtocolError(f"aparelho recusou a leitura (status {payload[:1].hex() or '?'})")
    if cmd != CMD_READ or len(payload) < 8:
        raise ProtocolError(f"resposta de leitura inesperada (comando 0x{cmd:02x})")
    got_region = payload[0]
    got_address = int.from_bytes(payload[1:5], "little")
    got_length = int.from_bytes(payload[5:8], "little")
    if (got_region, got_address, got_length) != (region, address, length):
        raise ProtocolError("a resposta não corresponde ao trecho pedido")
    data = payload[8:]
    if len(data) != length:
        raise ProtocolError("resposta com quantidade de dados diferente da declarada")
    return data


def parse_ack(frame: bytes) -> None:
    cmd, payload = parse_frame(frame)
    if cmd != CMD_ACK or len(payload) != 1:
        raise ProtocolError(f"confirmação inesperada (comando 0x{cmd:02x})")
    if payload[0] != 0:
        raise ProtocolError(f"aparelho recusou a escrita (status {payload[0]})")


# --- campos ------------------------------------------------------------------
# Cada campo: offset no registro, tamanho, rótulo, faixa do valor BRUTO e, se houver,
# um deslocamento de exibição (canal e oitava aparecem +1 na tela do Midi Suite).

def _f(offset: int, label: str, lo: int, hi: int, *, shift: int = 0, enum: Optional[list[str]] = None, size: int = 1) -> dict:
    return {"offset": offset, "size": size, "label": label, "lo": lo, "hi": hi, "shift": shift, "enum": enum}


FIELDS: dict[str, dict[str, dict]] = {
    "button": {
        "mode": _f(0, "Mode", 0, len(BUTTON_MODES) - 1, enum=BUTTON_MODES),
        "channel": _f(1, "Channel", 0, 15, shift=1),
        "cc": _f(2, "CC / Nota / Programa", 0, 127),
        "value1": _f(3, "Value 1", 0, 127),
        "value2": _f(4, "Value 2", 0, 127),
        "led": _f(22, "LED", 0, 255),
    },
    "knob": {
        "type": _f(0, "Type", 0, len(KNOB_TYPES) - 1, enum=KNOB_TYPES),
        "speed": _f(1, "Speed", 0, len(KNOB_SPEEDS) - 1, enum=KNOB_SPEEDS),
        "channel": _f(2, "Channel", 0, 15, shift=1),
        "cc": _f(3, "CC", 0, 127),
        "min": _f(4, "Min", 0, 127),
        "max": _f(5, "Max", 0, 127),
    },
    "pad": {
        "type": _f(0, "Type", 0, len(PAD_TYPES) - 1, enum=PAD_TYPES),
        "channel": _f(1, "Channel", 0, 15, shift=1),
        "note": _f(2, "Nota", 0, 127),
        "min_vel": _f(3, "MinVel", 0, 127),
        "max_vel": _f(4, "MaxVel", 0, 127),
        "led": _f(8, "LED", 0, 255),
    },
    # Valores conservadores: acima do que foi observado só com teste no aparelho.
    "globals": {
        "tempo": _f(0, "Tempo", 20, 300, size=2),
        "swing": _f(2, "Swing", 0, 100),
        "time": _f(3, "Time", 0, len(TIME_VALUES) - 1, enum=TIME_VALUES),
        "sync": _f(4, "Sync", 0, 1),
        "latch": _f(5, "Latch", 0, 1),
        "octave": _f(6, "Octave", 0, 7, shift=1),
        "transpose": _f(7, "Transpose", 0, 12),  # negativos ainda não observados
        "pad_curve": _f(8, "PadCurve", 0, 3, shift=1),
        "aftertouch": _f(9, "PadAftertouch", 0, 1),
        "preset": _f(10, "Preset atual", 0, PRESET_COUNT - 1, shift=1),
        "pad_bank": _f(11, "Banco de pads", 0, 1),
    },
}
# Só leitura: o aparelho muda sozinho (botão PAD BANK) e o valor é só um espelho.
READ_ONLY_FIELDS = {("globals", "pad_bank")}

_SYSEX_OFFSET = 5  # byte 5 = tamanho, 6..21 = dados (botão)
_COLOR_OFFSET = 5  # pad: R, G, B


def _read_field(data: bytes, base: int, spec: dict) -> int:
    off = base + spec["offset"]
    return int.from_bytes(data[off : off + spec["size"]], "little") + spec["shift"]


def _record(kind: str, data: bytes, base: int) -> dict[str, Any]:
    rec = {name: _read_field(data, base, spec) for name, spec in FIELDS[kind].items()}
    if kind == "button":
        size = min(data[base + _SYSEX_OFFSET], SYSEX_MAX)
        raw = data[base + _SYSEX_OFFSET + 1 : base + _SYSEX_OFFSET + 1 + size]
        rec["sysex"] = " ".join(f"{b:02X}" for b in raw)
    if kind == "pad":
        r, g, b = data[base + _COLOR_OFFSET : base + _COLOR_OFFSET + 3]
        rec["color"] = f"#{r:02x}{g:02x}{b:02x}"
        rec["extra"] = data[base + 9 : base + PAD_SIZE].hex()
    return rec


def parse_globals(data: bytes) -> dict[str, Any]:
    if len(data) != GLOBALS_SIZE:
        raise ProtocolError(f"globais devem ter {GLOBALS_SIZE} bytes, vieram {len(data)}")
    return {name: _read_field(data, 0, spec) for name, spec in FIELDS["globals"].items()} | {"raw": data.hex(" ")}


def level_to_raw(level: int) -> int:
    """Nível 1..8 do Midi Suite -> valor gravado (50, 150, ..., 750)."""
    return 50 + 100 * (level - 1)


def raw_to_level(raw: int) -> int:
    """Como a tela do Midi Suite mostra o valor (220 de fábrica aparece como Level 2)."""
    return max(1, min(CALIBRATION_LEVELS, (raw - 50) // 100 + 1))


def parse_calibration(data: bytes) -> list[dict[str, Any]]:
    if len(data) != CALIBRATION_SIZE:
        raise ProtocolError(f"calibração deve ter {CALIBRATION_SIZE} bytes, vieram {len(data)}")
    pads = []
    for i in range(CALIBRATION_PADS):
        raw = int.from_bytes(data[i * CALIBRATION_STRIDE : i * CALIBRATION_STRIDE + 2], "little")
        pads.append({"pad": i + 1, "raw": raw, "level": raw_to_level(raw)})
    return pads


def preset_slice(image: bytes, preset: int) -> bytes:
    if not 1 <= preset <= PRESET_COUNT:
        raise ProtocolError(f"preset {preset} fora de 1..{PRESET_COUNT}")
    return image[(preset - 1) * PRESET_SIZE : preset * PRESET_SIZE]


def parse_preset(data: bytes) -> dict[str, Any]:
    if len(data) != PRESET_SIZE:
        raise ProtocolError(f"preset deve ter {PRESET_SIZE} bytes, veio {len(data)}")
    buttons = []
    for i in range(BUTTON_COUNT):
        rec = _record("button", data, BUTTON_BASE + i * BUTTON_SIZE)
        rec["index"], rec["name"] = i, BUTTON_NAMES[i]
        buttons.append(rec)
    knobs = []
    for i in range(KNOB_COUNT):
        rec = _record("knob", data, KNOB_BASE + i * KNOB_SIZE)
        rec["index"], rec["bank"], rec["number"] = i, i // 8 + 1, i % 8 + 1
        knobs.append(rec)
    pads = []
    for i in range(PAD_COUNT):
        rec = _record("pad", data, PAD_BASE + i * PAD_SIZE)
        rec["index"], rec["group"], rec["number"] = i, i // PAD_GROUP_SIZE + 1, i % PAD_GROUP_SIZE + 1
        pads.append(rec)
    return {"buttons": buttons, "knobs": knobs, "pads": pads}


def enums() -> dict[str, list[str]]:
    return {
        "button_modes": BUTTON_MODES,
        "pad_types": PAD_TYPES,
        "knob_types": KNOB_TYPES,
        "knob_speeds": KNOB_SPEEDS,
        "time": TIME_VALUES,
    }


# --- edições -----------------------------------------------------------------

class EditError(ValueError):
    """Edição inválida (campo desconhecido, fora da faixa, alvo inexistente)."""


def _record_base(kind: str, index: int) -> int:
    if kind == "button":
        count, size, base = BUTTON_COUNT, BUTTON_SIZE, BUTTON_BASE
    elif kind == "knob":
        count, size, base = KNOB_COUNT, KNOB_SIZE, KNOB_BASE
    else:
        count, size, base = PAD_COUNT, PAD_SIZE, PAD_BASE
    if not isinstance(index, int) or not 0 <= index < count:
        raise EditError(f"{kind} {index} fora de 0..{count - 1}")
    return base + index * size


def _hex_color(value: Any) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()):
        raise EditError("cor deve ser #RRGGBB")
    return bytes.fromhex(value.strip().lstrip("#"))


def _sysex_bytes(value: Any) -> bytes:
    if not isinstance(value, str):
        raise EditError("SysEx deve ser texto em hexadecimal")
    tokens = re.findall(r"[0-9a-fA-F]{2}", value)
    if re.sub(r"[0-9a-fA-F\s,]", "", value) or len("".join(tokens)) != len(re.sub(r"[\s,]", "", value)):
        raise EditError("SysEx deve ter bytes em hexadecimal (ex.: F0 7F 7F 06 02 F7)")
    data = bytes(int(t, 16) for t in tokens)
    if len(data) > SYSEX_MAX:
        raise EditError(f"SysEx aceita no máximo {SYSEX_MAX} bytes")
    return data


def plan_edit(edit: dict[str, Any]) -> tuple[int, int, bytes]:
    """Edição (valores como na tela) -> (região, endereço, bytes brutos a gravar).

    Formato: {"scope": "button|knob|pad|globals", "preset": 1..8, "index": n,
    "field": nome, "value": valor}. Para pad existe "color" (#RRGGBB) e para botão
    "sysex" (hex), que o Midi Suite grava em um único bloco.
    """
    scope, field, value = edit.get("scope"), edit.get("field"), edit.get("value")
    if scope == "calibration":
        pad = edit.get("pad")
        if not isinstance(pad, int) or isinstance(pad, bool) or not 1 <= pad <= CALIBRATION_PADS:
            raise EditError(f"pad deve estar em 1..{CALIBRATION_PADS}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise EditError("calibration deve ser um número inteiro")
        if field == "level":
            if not 1 <= value <= CALIBRATION_LEVELS:
                raise EditError(f"nível fora da faixa 1..{CALIBRATION_LEVELS}")
            value = level_to_raw(value)
        elif field == "raw":
            if not 50 <= value <= 750:  # faixa que o Midi Suite grava (níveis 1..8)
                raise EditError("valor bruto fora da faixa 50..750")
        else:
            raise EditError(f"campo desconhecido: calibration.{field}")
        return REGION_CALIBRATION, (pad - 1) * CALIBRATION_STRIDE, value.to_bytes(2, "little")
    if scope not in FIELDS:
        raise EditError(f"escopo desconhecido: {scope!r}")
    if (scope, field) in READ_ONLY_FIELDS:
        raise EditError(f"{scope}.{field} é somente leitura")

    if scope == "globals":
        spec = FIELDS["globals"].get(field)
        if spec is None:
            raise EditError(f"campo desconhecido: globals.{field}")
        return REGION_GLOBALS, spec["offset"], _encode(spec, value, f"globals.{field}")

    preset = edit.get("preset")
    if not isinstance(preset, int) or not 1 <= preset <= PRESET_COUNT:
        raise EditError(f"preset deve estar em 1..{PRESET_COUNT}")
    base = (preset - 1) * PRESET_SIZE + _record_base(scope, edit.get("index"))

    if scope == "pad" and field == "color":
        return REGION_CONFIG, base + _COLOR_OFFSET, _hex_color(value)
    if scope == "button" and field == "sysex":
        data = _sysex_bytes(value)
        return REGION_CONFIG, base + _SYSEX_OFFSET, bytes([len(data)]) + data.ljust(SYSEX_MAX, b"\x00")
    spec = FIELDS[scope].get(field)
    if spec is None:
        raise EditError(f"campo desconhecido: {scope}.{field}")
    return REGION_CONFIG, base + spec["offset"], _encode(spec, value, f"{scope}.{field}")


def _encode(spec: dict, value: Any, name: str) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EditError(f"{name} deve ser um número inteiro")
    raw = value - spec["shift"]
    if not spec["lo"] <= raw <= spec["hi"]:
        lo, hi = spec["lo"] + spec["shift"], spec["hi"] + spec["shift"]
        raise EditError(f"{name} fora da faixa {lo}..{hi}")
    return raw.to_bytes(spec["size"], "little")


def apply_to_image(image: bytearray, region: int, address: int, data: bytes) -> None:
    """Aplica uma escrita planejada a uma cópia local da memória (para pré-visualizar/verificar)."""
    if region == REGION_CONFIG:
        if address + len(data) > CONFIG_SIZE:
            raise EditError("escrita fora da região de configuração")
        image[address : address + len(data)] = data
    elif region == REGION_GLOBALS:
        if address + len(data) > GLOBALS_SIZE:
            raise EditError("escrita fora dos globais")
        image[address : address + len(data)] = data
    elif region == REGION_CALIBRATION:
        if address + len(data) > CALIBRATION_SIZE:
            raise EditError("escrita fora da calibração")
        image[address : address + len(data)] = data
    else:
        raise EditError(f"região {region} desconhecida")

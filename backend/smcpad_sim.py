"""Simulador do SMC-PAD: fala o protocolo real, sem MIDI nem hardware.

Usado nos testes (backend e MCP) e serve para desenvolver a aba SMCPAD Config sem o
aparelho. A memória inicial imita o padrão de fábrica na estrutura observada.
"""
from __future__ import annotations

from typing import Optional

import smcpad as sp

FACTORY_GLOBALS = bytes.fromhex("780032040000020001010000")
FACTORY_CALIBRATION = bytes.fromhex("dc00 2602 d002 5203 b603".replace(" ", "")) * 16  # 220, 550, 720, 850, 950


def make_preset() -> bytes:
    """Preset sintético com a estrutura observada (fábrica simplificada)."""
    data = bytearray(sp.PRESET_SIZE)
    for i in range(sp.BUTTON_COUNT):
        base = sp.BUTTON_BASE + i * sp.BUTTON_SIZE
        data[base : base + 5] = bytes([2, 0, 25 + i, 0, 127])
        data[base + 5] = 6
        data[base + 6 : base + 12] = bytes([0xF0, 0x7F, 0x7F, 0x06, i + 1, 0xF7])
        data[base + 22] = 255
    for i in range(sp.KNOB_COUNT):
        base = sp.KNOB_BASE + i * sp.KNOB_SIZE
        data[base : base + 6] = bytes([0, 2, 0, 30 + i, 0, 127])
    colors = [b"\x96\xc8\xf0", b"\xf0\xf0\x00", b"\xf0\x00\xf0", b"\x00\x00\xf0", b"\x00\xf0\x00", b"\xf0\x00\x00", b"\x00\xf0\xf0", b"\x78\x78\xf0"]
    for i in range(sp.PAD_COUNT):
        base = sp.PAD_BASE + i * sp.PAD_SIZE
        note = 4 + i if i < 112 else 52 + i - 112
        data[base : base + 9] = bytes([0, 9, note, 0, 127]) + colors[i // 16] + bytes([255])
    return bytes(data)


def make_image() -> bytes:
    return b"".join(make_preset() for _ in range(sp.PRESET_COUNT))


class FakeSmcPad:
    """Transporte simulado: memória em 3 regiões; responde identificação/leitura/escrita/commit."""

    def __init__(self, *, drop: int = 0, corrupt_next_write: bool = False) -> None:
        self.regions = {
            sp.REGION_CALIBRATION: bytearray(FACTORY_CALIBRATION),
            sp.REGION_GLOBALS: bytearray(FACTORY_GLOBALS),
            sp.REGION_CONFIG: bytearray(make_image()),
        }
        self.drop = drop  # quantas respostas "perder" (simula falha de comunicação)
        self.corrupt_next_write = corrupt_next_write  # grava errado uma vez (testa a conferência)
        self.commits: list[int] = []
        self.writes: list[tuple[int, int, bytes]] = []
        self.closed = 0

    def exchange(self, sysex: bytes, timeout: float = 1.0) -> Optional[bytes]:
        if self.drop > 0:
            self.drop -= 1
            return None
        cmd, payload = sp.parse_frame(sp.from_sysex(sysex))
        if cmd == sp.CMD_IDENTIFY:
            reply = sp.build_frame(sp.CMD_IDENTIFY, b"SMC-PAD_004" + bytes(16))
        elif cmd == sp.CMD_READ:
            region, address, n = payload[0], int.from_bytes(payload[1:5], "little"), int.from_bytes(payload[5:8], "little")
            mem = self.regions.get(region)
            if mem is None or address + n > len(mem) or n > sp.READ_CHUNK:
                reply = sp.build_frame(sp.CMD_ACK, b"\x01")
            else:
                reply = sp.build_frame(sp.CMD_READ, payload[:8] + bytes(mem[address : address + n]))
        elif cmd == sp.CMD_WRITE:
            region, address, n = payload[0], int.from_bytes(payload[1:5], "little"), int.from_bytes(payload[5:8], "little")
            data = payload[8:]
            mem = self.regions.get(region)
            if n == 0:
                self.commits.append(region)
                reply = sp.build_frame(sp.CMD_ACK, b"\x00")
            elif mem is None or address + n > len(mem) or n > sp.WRITE_CHUNK:
                reply = sp.build_frame(sp.CMD_ACK, b"\x01")
            else:
                self.writes.append((region, address, data))
                stored = bytes([data[0] ^ 0x01]) + data[1:] if self.corrupt_next_write else data
                self.corrupt_next_write = False
                mem[address : address + n] = stored
                reply = sp.build_frame(sp.CMD_ACK, b"\x00")
        else:
            reply = sp.build_frame(sp.CMD_ACK, b"\x01")
        return sp.to_sysex(reply)

    def close(self) -> None:
        self.closed += 1

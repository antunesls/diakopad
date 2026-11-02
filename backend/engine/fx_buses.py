"""Shared effect buses: one hall reverb, one plate reverb and one
tempo-synced delay, fed by a send level per pad.

The idea comes from Hydrogen (song-level FX slots) and drumkv1 (per-element
"OUT FX send" into a global effects stage). Per-pad insert effects (one LV2
instance per pad/slot, see effects_catalog.py) don't scale: 20+ Dragonfly
instances crashed mod-host when a scene switch removed them, and each one
costs a full reverb's CPU. Here every plugin below is created ONCE, when the
engine starts, and never removed - a scene switch only changes parameters.

JACK topology (instances fixed, see orchestrator.py):

  pad 1..8  final out L+R --> effect_8 (x42 matrix 8x8)  in1..in8
  pad 9..16 final out L+R --> effect_9 (x42 matrix 8x8)  in1..in8
  matrix out1 --> hall in   (both matrices sum into it)
  matrix out2 --> plate in
  matrix out3 --> delay in
  hall/plate/delay out L/R --> master

A pad's two channels are summed into one matrix input (JACK sums ports), so
the send is mono; the reverbs/delay make it stereo again. The send gain
compensates that 2x sum.

Validated on the Ubuntu Studio laptop with a silent impulse probe
(nov/2026): matrix symbols are mix_<input>_<output> with linear gains;
Dragonfly with dry_level=0 outputs only the wet signal; the LSP slapback
delay with `dm`=1 (dry mute) outputs only echoes, at the exact dt0 time
(750 ms asked -> 753 ms measured) and dfb0 feedback repeats. The mda Delay
used by the per-pad catalog was rejected for this: its l_delay is a
non-linear 0..1 knob (0.5 -> 189 ms) and r_delay is a ratio of L.
"""
from __future__ import annotations

import os
from typing import Optional

import storage

BUSES = storage.FX_BUSES  # ("hall", "plate", "delay")

MATRIX_URI = os.environ.get("DIAKOPAD_FX_MATRIX_LV2_URI", "http://gareus.org/oss/lv2/matrixmixer#i8o8")
MATRIX_INPUTS = 8
# matrix output (1-based) each bus is fed from
BUS_MATRIX_OUTPUT = {"hall": 1, "plate": 2, "delay": 3}

DRAGONFLY_IN = ("lv2_audio_in_1", "lv2_audio_in_2")
DRAGONFLY_OUT = ("lv2_audio_out_1", "lv2_audio_out_2")

# Delay divisions in beats (quarter note = 1).
DELAY_DIVISIONS = [
    {"value": 1.5, "label": "1/4 pontuada"},
    {"value": 1.0, "label": "1/4"},
    {"value": 2 / 3, "label": "1/4 tercina"},
    {"value": 0.75, "label": "1/8 pontuada"},
    {"value": 0.5, "label": "1/8"},
    {"value": 0.375, "label": "1/16 pontuada"},
    {"value": 0.25, "label": "1/16"},
]
DELAY_MAX_MS = 1000.0  # LSP slapback dt0 range

BUS_CATALOG: dict[str, dict] = {
    "hall": {
        "label": "Reverb Hall",
        "lv2_uri": os.environ.get("DIAKOPAD_FX_BUS_HALL_LV2_URI", "https://github.com/michaelwillis/dragonfly-reverb"),
        "in_ports": DRAGONFLY_IN,
        "out_ports": DRAGONFLY_OUT,
        # Bus = wet only: the dry sound already reaches the master directly.
        "fixed": {"dry_level": 0.0, "early_level": 10.0},
        "params": [
            {"symbol": "late_level", "label": "Nível", "unit": "%", "min": 0.0, "max": 100.0, "default": 100.0, "curve": "linear"},
            {"symbol": "decay", "label": "Decay", "unit": "s", "min": 0.5, "max": 10.0, "default": 4.0, "curve": "linear"},
            {"symbol": "size", "label": "Tamanho", "unit": "m", "min": 10.0, "max": 60.0, "default": 45.0, "curve": "linear"},
            {"symbol": "delay", "label": "Pre-delay", "unit": "ms", "min": 0.0, "max": 100.0, "default": 20.0, "curve": "linear"},
            {"symbol": "low_cut", "label": "Cut graves", "unit": "Hz", "min": 0.0, "max": 200.0, "default": 150.0, "curve": "linear"},
            {"symbol": "high_cut", "label": "Cut agudos", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 7000.0, "curve": "log"},
            {"symbol": "width", "label": "Largura", "unit": "%", "min": 50.0, "max": 150.0, "default": 120.0, "curve": "linear"},
        ],
    },
    "plate": {
        "label": "Reverb Plate",
        "lv2_uri": os.environ.get("DIAKOPAD_FX_BUS_PLATE_LV2_URI", "urn:dragonfly:plate"),
        "in_ports": DRAGONFLY_IN,
        "out_ports": DRAGONFLY_OUT,
        "fixed": {"dry_level": 0.0},
        "params": [
            {"symbol": "early_level", "label": "Nível", "unit": "%", "min": 0.0, "max": 100.0, "default": 100.0, "curve": "linear"},
            {"symbol": "decay", "label": "Decay", "unit": "s", "min": 0.1, "max": 10.0, "default": 1.8, "curve": "linear"},
            {"symbol": "predelay", "label": "Pre-delay", "unit": "ms", "min": 0.0, "max": 100.0, "default": 15.0, "curve": "linear"},
            {"symbol": "low_cut", "label": "Cut graves", "unit": "Hz", "min": 0.0, "max": 200.0, "default": 200.0, "curve": "linear"},
            {"symbol": "high_cut", "label": "Cut agudos", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 10000.0, "curve": "log"},
            {"symbol": "early_damp", "label": "Damping", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 10000.0, "curve": "log"},
        ],
    },
    "delay": {
        "label": "Delay (segue o BPM)",
        "lv2_uri": os.environ.get("DIAKOPAD_FX_BUS_DELAY_LV2_URI", "http://lsp-plug.in/plugins/lv2/slap_delay_stereo"),
        "in_ports": ("in_l", "in_r"),
        "out_ports": ("out_l", "out_r"),
        # dm=1: dry muted (wet only). dm0=1: line 0 in "time" mode, with
        # dt0 computed from the tempo (see delay_time_ms); eq0/lfc0/hfc0 turn
        # on that line's low/high cut so repeats don't muddy the mix.
        "fixed": {"enabled": 1.0, "dm": 1.0, "drywet": 100.0, "dm0": 1.0, "dg0": 1.0,
                  "eq0": 1.0, "lfc0": 1.0, "hfc0": 1.0},
        "params": [
            {"symbol": "wet", "label": "Nível", "unit": "", "min": 0.0, "max": 2.0, "default": 1.0, "curve": "linear"},
            {"symbol": "beats", "label": "Divisão", "unit": "", "min": 0.25, "max": 1.5, "default": 0.75,
             "curve": "stepped", "options": DELAY_DIVISIONS, "derived": True},
            {"symbol": "dfb0", "label": "Repetições", "unit": "", "min": 0.0, "max": 0.9, "default": 0.35, "curve": "linear"},
            {"symbol": "flc0", "label": "Cut graves", "unit": "Hz", "min": 10.0, "max": 1000.0, "default": 200.0, "curve": "log"},
            {"symbol": "fhc0", "label": "Cut agudos", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 6000.0, "curve": "log"},
        ],
    },
}


def bus_param(bus: str, symbol: str) -> Optional[dict]:
    return next((p for p in BUS_CATALOG[bus]["params"] if p["symbol"] == symbol), None)


def effective_params(bus: str, stored: Optional[dict]) -> dict[str, float]:
    """Defaults overlaid with stored values, clamped to range; unknown
    symbols are dropped."""
    result = {p["symbol"]: float(p["default"]) for p in BUS_CATALOG[bus]["params"]}
    for p in BUS_CATALOG[bus]["params"]:
        value = (stored or {}).get(p["symbol"])
        if value is None:
            continue
        try:
            result[p["symbol"]] = max(float(p["min"]), min(float(p["max"]), float(value)))
        except (TypeError, ValueError):
            continue
    return result


def delay_time_ms(bpm: float, beats: float) -> float:
    """Delay line time for `beats` (quarter = 1) at `bpm`, clamped to the
    plugin's 0..1000 ms range (a dotted eighth stays exact down to 45 BPM)."""
    return max(1.0, min(DELAY_MAX_MS, beats * 60000.0 / max(bpm, 1.0)))


def plugin_values(bus: str, params: dict[str, float], bpm: float) -> dict[str, float]:
    """Every LV2 control value to send for a bus: fixed ones, the user params,
    and derived ones (the delay's `beats` becomes dt0 in ms)."""
    values = dict(BUS_CATALOG[bus]["fixed"])
    for symbol, value in params.items():
        meta = bus_param(bus, symbol)
        if meta and meta.get("derived"):
            continue
        values[symbol] = value
    if bus == "delay":
        values["dt0"] = delay_time_ms(bpm, params.get("beats", 0.75))
    return values


def send_gain(level: float) -> float:
    """0..100% send -> linear matrix gain. Squared taper (50% = -12 dB,
    70% = -6 dB) feels even on a slider; x0.5 compensates the L+R sum into
    the single matrix input, so 100% is unity for a centered sound."""
    frac = max(0.0, min(100.0, float(level))) / 100.0
    return 0.5 * frac * frac


def matrix_slot(pad_number: int) -> tuple[int, int]:
    """(matrix index 0/1, 1-based input) for a pad."""
    return (pad_number - 1) // MATRIX_INPUTS, (pad_number - 1) % MATRIX_INPUTS + 1


def list_catalog() -> list[dict]:
    return [
        {"bus": bus, "label": entry["label"], "params": entry["params"]}
        for bus, entry in BUS_CATALOG.items()
    ]

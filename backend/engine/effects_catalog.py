"""Curated catalog of per-pad effect slot plugins.

Deliberately a small, curated set rather than "browse every installed LV2
plugin" - keeps the UI and the mod-host wiring in orchestrator.py simple.
Each entry's LV2 URI and port names are env-overridable (same convention as
the rest of backend/engine/) since the exact plugin available on a given
Zynthian OS install is only knowable on-device via `lv2ls`/`lv2info` - see
deploy/install.sh. The parameter list (symbols/ranges) is NOT env-overridable
since that's a lot of surface area; hand-edit this file once a real plugin
has been picked for a catalog slot on the target device.
"""
from __future__ import annotations

import os


def _ports(env_prefix: str, default_in: str, default_out: str) -> tuple[tuple[str, str], tuple[str, str]]:
    in_ports = tuple(os.environ.get(f"{env_prefix}_IN_PORTS", default_in).split(","))
    out_ports = tuple(os.environ.get(f"{env_prefix}_OUT_PORTS", default_out).split(","))
    return in_ports, out_ports  # type: ignore[return-value]


def _entry(env_prefix: str, label: str, params: list[dict]) -> dict:
    in_ports, out_ports = _ports(env_prefix, "in_l,in_r", "out_l,out_r")
    return {
        "label": label,
        "lv2_uri": os.environ.get(f"{env_prefix}_LV2_URI"),
        "in_ports": in_ports,
        "out_ports": out_ports,
        "params": params,
    }


PLUGIN_CATALOG: dict[str, dict] = {
    "reverb": _entry(
        "DIAKOPAD_FX_REVERB",
        "Reverb",
        [
            {"symbol": "mix", "label": "Mix", "unit": "%", "min": 0, "max": 100, "default": 30, "curve": "linear"},
            {"symbol": "room_size", "label": "Tamanho", "unit": "%", "min": 0, "max": 100, "default": 50, "curve": "linear"},
            {"symbol": "damping", "label": "Damping", "unit": "%", "min": 0, "max": 100, "default": 50, "curve": "linear"},
        ],
    ),
    "delay": _entry(
        "DIAKOPAD_FX_DELAY",
        "Delay",
        [
            {"symbol": "mix", "label": "Mix", "unit": "%", "min": 0, "max": 100, "default": 30, "curve": "linear"},
            {"symbol": "time", "label": "Tempo", "unit": "s", "min": 0.01, "max": 2.0, "default": 0.25, "curve": "linear"},
            {"symbol": "feedback", "label": "Feedback", "unit": "%", "min": 0, "max": 95, "default": 30, "curve": "linear"},
        ],
    ),
    "compressor": _entry(
        "DIAKOPAD_FX_COMPRESSOR",
        "Compressor",
        [
            {"symbol": "threshold", "label": "Threshold", "unit": "dB", "min": -60, "max": 0, "default": -18, "curve": "linear"},
            {"symbol": "ratio", "label": "Ratio", "unit": ":1", "min": 1, "max": 20, "default": 4, "curve": "linear"},
            {"symbol": "makeup", "label": "Makeup", "unit": "dB", "min": 0, "max": 24, "default": 0, "curve": "linear"},
        ],
    ),
    "drive": _entry(
        "DIAKOPAD_FX_DRIVE",
        "Overdrive",
        [
            {"symbol": "amount", "label": "Quantidade", "unit": "%", "min": 0, "max": 100, "default": 30, "curve": "linear"},
            {"symbol": "tone", "label": "Tom", "unit": "%", "min": 0, "max": 100, "default": 50, "curve": "linear"},
        ],
    ),
    "eq3": _entry(
        "DIAKOPAD_FX_EQ3",
        "EQ 3 bandas",
        [
            {"symbol": "low", "label": "Graves", "unit": "dB", "min": -24, "max": 24, "default": 0, "curve": "linear"},
            {"symbol": "mid", "label": "Médios", "unit": "dB", "min": -24, "max": 24, "default": 0, "curve": "linear"},
            {"symbol": "high", "label": "Agudos", "unit": "dB", "min": -24, "max": 24, "default": 0, "curve": "linear"},
        ],
    ),
}


def default_params(plugin_id: str) -> dict[str, float]:
    plugin = PLUGIN_CATALOG.get(plugin_id)
    if not plugin:
        return {}
    return {p["symbol"]: p["default"] for p in plugin["params"]}


def list_catalog() -> list[dict]:
    """Serializable form for the frontend (GET /api/effects/catalog)."""
    return [{"plugin_id": plugin_id, **entry} for plugin_id, entry in PLUGIN_CATALOG.items()]

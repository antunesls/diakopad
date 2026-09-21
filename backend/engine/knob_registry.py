"""Single source of truth for what a physical knob can be assigned to.

Drives three things that must stay in sync: the Knobs tab's two-step
picker (GET /api/knobs/targets), LearnRequest validation, and the
CC-to-value scaling used when a mapped knob is turned (replacing the old
fixed _cc_to_volume_db/_cc_to_pan/_cc_to_cutoff helpers in app.py).

Pad-native params (volume/pan/tone) always exist; per-slot effect params
are dynamic, sourced from effects_catalog.py for whatever plugin (if any)
currently occupies each of a pad's slots - which is exactly why this can't
be a static SQLite CHECK constraint (see storage.py's knob_mappings table).
"""
from __future__ import annotations

from typing import Optional, TypedDict

from engine import effects_catalog


class ParamMeta(TypedDict):
    param: str
    label: str
    unit: str
    min: float
    max: float
    curve: str  # "linear" | "log" | "stepped" (discrete stops, e.g. looper track 1-4)
    bypass_at_max: bool  # only "tone" uses this: >=99% turn = filter bypass (None cutoff)


PAD_NATIVE_PARAMS: list[ParamMeta] = [
    {"param": "volume", "label": "Volume", "unit": "dB", "min": -24, "max": 12, "curve": "linear", "bypass_at_max": False},
    {"param": "pan", "label": "Pan", "unit": "", "min": -100, "max": 100, "curve": "linear", "bypass_at_max": False},
    {"param": "tone", "label": "Tom", "unit": "Hz", "min": 200, "max": 20000, "curve": "log", "bypass_at_max": True},
]

GLOBAL_PARAMS: list[ParamMeta] = [
    {"param": "tempo", "label": "Tempo", "unit": "bpm", "min": 40, "max": 240, "curve": "linear", "bypass_at_max": False},
    {"param": "looper_track", "label": "Looper — Trilha", "unit": "", "min": 1, "max": 4, "curve": "stepped", "bypass_at_max": False},
    {"param": "looper_mute", "label": "Looper — Mute da Trilha", "unit": "", "min": 0, "max": 1, "curve": "stepped", "bypass_at_max": False},
]


def list_targets() -> list[dict]:
    return [{"scope": "global", "label": "Global"}] + [
        {"scope": "pad", "pad_number": n, "label": f"Pad {n}"} for n in range(1, 17)
    ]


def _slot_params(pad_number: int, pad_effects: list[dict]) -> list[ParamMeta]:
    metas: list[ParamMeta] = []
    for row in pad_effects:
        if row["pad_number"] != pad_number or not row.get("plugin_id"):
            continue
        plugin = effects_catalog.PLUGIN_CATALOG.get(row["plugin_id"])
        if not plugin:
            continue
        for p in plugin["params"]:
            metas.append(
                {
                    "param": f"slot{row['slot_index']}:{p['symbol']}",
                    "label": f"{plugin['label']} — {p['label']}",
                    "unit": p.get("unit", ""),
                    "min": p["min"],
                    "max": p["max"],
                    "curve": p.get("curve", "linear"),
                    "bypass_at_max": False,
                }
            )
    return metas


def list_params(scope: str, pad_number: Optional[int], pad_effects: list[dict]) -> list[ParamMeta]:
    if scope == "global":
        return list(GLOBAL_PARAMS)
    if scope == "pad" and pad_number is not None:
        return list(PAD_NATIVE_PARAMS) + _slot_params(pad_number, pad_effects)
    return []


def resolve(
    scope: str, pad_number: Optional[int], param: str, pad_effects: list[dict]
) -> Optional[ParamMeta]:
    for meta in list_params(scope, pad_number, pad_effects):
        if meta["param"] == param:
            return meta
    return None


def cc_to_value(cc_value: int, meta: ParamMeta) -> Optional[float]:
    frac = cc_value / 127
    if meta["bypass_at_max"] and frac >= 0.99:
        return None
    lo, hi = meta["min"], meta["max"]
    if meta["curve"] == "stepped":
        return lo + round(frac * (hi - lo))
    if meta["curve"] == "log":
        return lo * (hi / lo) ** frac
    return lo + frac * (hi - lo)

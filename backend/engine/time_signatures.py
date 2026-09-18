"""Catalog of metronome time signatures: how many audible pulses make up a
bar and which of those pulses get the accented click (see
engine/metronome_sounds.py for the accent/normal sounds themselves).

Simple meters (2/4, 3/4, 4/4, 5/4) accent only the downbeat. Compound and
additive meters (6/8, 9/8, 12/8: groups of 3; 7/8: a 3+2+2 grouping) accent
the start of every group, so they actually feel like the meter they're
named after instead of a bigger simple meter with more clicks.

Deliberate simplification: every pulse is spaced at the same fixed
60/BPM interval regardless of signature - only the pulse count and accent
placement change with the signature. This doesn't model "dotted quarter =
BPM" for compound meters, which is genuinely ambiguous without a specific
convention to follow and wasn't asked for.
"""
from __future__ import annotations

SIGNATURES: dict[str, dict] = {
    "2_4": {"label": "2/4", "pulses": 2, "accents": [0]},
    "3_4": {"label": "3/4", "pulses": 3, "accents": [0]},
    "4_4": {"label": "4/4", "pulses": 4, "accents": [0]},
    "5_4": {"label": "5/4", "pulses": 5, "accents": [0]},
    "6_8": {"label": "6/8", "pulses": 6, "accents": [0, 3]},
    "7_8": {"label": "7/8", "pulses": 7, "accents": [0, 3, 5]},  # 3+2+2 grouping
    "9_8": {"label": "9/8", "pulses": 9, "accents": [0, 3, 6]},
    "12_8": {"label": "12/8", "pulses": 12, "accents": [0, 3, 6, 9]},
}
DEFAULT_SIGNATURE = "4_4"


def list_signatures() -> list[dict]:
    return [
        {"signature": key, "label": v["label"], "pulses": v["pulses"], "accents": v["accents"]}
        for key, v in SIGNATURES.items()
    ]


def get(signature: str) -> dict:
    return SIGNATURES.get(signature, SIGNATURES[DEFAULT_SIGNATURE])

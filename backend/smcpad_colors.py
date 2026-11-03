"""Kit de cores do SMC-PAD por tipo de som.

Classifica o nome (e a pasta) do som de cada pad do DiakoPad — bumbo, caixa, hi-hat,
prato, tom, percussão... — e devolve uma cor por tipo, para os pads do mesmo tipo
ficarem da mesma cor no aparelho. Puro (sem MIDI, sem banco): usado pela aba
SMCPAD Config e pelo MCP para gerar as cores de uma cena.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

# Ordem importa: a primeira categoria que casa vence (ex.: "ride_DryBell" é prato, não sino).
# Cada padrão procura palavras inteiras (tokens) ou radicais claros, já sem acento e em minúsculas.
CATEGORIES: list[tuple[str, str, str, str]] = [
    # (chave, rótulo, cor, padrão)
    ("kick", "Bumbo", "#0040f0", r"kick|kik|bumbo|bass ?drum|\bbd\b|\d+bd\b"),
    ("snare", "Caixa", "#f00000", r"snare|\bsn\d*\b|nare|caixa|\bsnr\b"),
    ("rim", "Rim / stick", "#f05000", r"stick|\brim|woody|side ?stick"),
    ("clap", "Palmas / estalos", "#f000a0", r"clap|snap|palma"),
    ("hihat", "Hi-hat / chimbal", "#f0e000", r"hi ?hat|\bhat\b|\bhh[co]?\b|chimbal|charleston"),
    ("cymbal", "Pratos", "#00e0f0", r"cymbal|crash|\bride\d*\b|\bcra\b|\bcr\d+|splash|china|prato"),
    ("tom", "Tons", "#00f040", r"\btom|\b[hml]tm|tmmf|surdo|floor"),
    ("bell", "Sinos / metais", "#c8c8f0", r"triang|chime|marktree|bell|agog|carrilh|sino|campan"),
    ("perc", "Percussão", "#a000f0", r"bongo|conga|timp|tymp|pand[ei]+ro|tambourine|\btamb|shaker|perc|cabasa|guiro|clave"),
    ("laugh", "Risos", "#b4f000", r"laugh|giggl|crowd|\bhee\b|risada|risos"),
    ("fail", "Falhas / gritos", "#ff2020", r"fail|errou|game ?over|trombone|\bsad\b|scream|falling|buzzer|wrong"),
    ("blip", "Apitos / cliques", "#00f0b0", r"whistle|\btoy\b|\bpop\b|click|beep|\bbip\b"),
]
OTHER = ("other", "Outros / vozes", "#b0b0b0")
EMPTY = ("empty", "Pad vazio", "#141414")

_COMPILED = [(key, re.compile(pattern)) for key, _label, _color, pattern in CATEGORIES]
_COLORS = {key: color for key, _l, color, _p in CATEGORIES} | {OTHER[0]: OTHER[2], EMPTY[0]: EMPTY[2]}
_LABELS = {key: label for key, label, _c, _p in CATEGORIES} | {OTHER[0]: OTHER[1], EMPTY[0]: EMPTY[1]}

BANK_A_FIRST_INDEX = 32  # registro do pad de nota 36 no grupo 3 (banco A de fábrica)
BANK_A_FIRST_NOTE = 36


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def classify(name: Optional[str], folder: Optional[str] = None) -> str:
    """Tipo do som ('kick', 'snare', ... 'other'); 'empty' quando o pad não tem som."""
    if not name:
        return EMPTY[0]
    # separa letras/dígitos coladas só no radical de que precisamos (ex.: "Sn3" -> "sn3" continua token)
    for text in (_normalize(name), _normalize(folder or "")):
        if not text:
            continue
        for key, pattern in _COMPILED:
            if pattern.search(text):
                return key
    return OTHER[0]


def legend() -> list[dict[str, str]]:
    return [{"key": k, "label": _LABELS[k], "color": _COLORS[k]} for k in [c[0] for c in CATEGORIES] + [OTHER[0], EMPTY[0]]]


def default_base() -> list[str]:
    """Cores dos registros que o kit não toca (fora das 16 notas dos pads do DiakoPad)."""
    return [EMPTY[2]] * 128


def kit_colors(
    pad_sounds: list[dict[str, Any]],
    base: Optional[list[str]] = None,
    note_to_index: Optional[dict[int, int]] = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Gera as 128 cores: parte de `base` e pinta os pads do DiakoPad por tipo de som.

    pad_sounds: [{"pad_number", "midi_note", "name", "folder"}, ...]. O pad vai para o registro do
    SMC-PAD cuja nota é a `midi_note` dele (note_to_index, lido do aparelho); sem esse mapa vale o
    padrão de fábrica (notas 36–51 no grupo 3). Devolve (128 cores, detalhe por pad)."""
    colors = list(base) if base and len(base) == 128 else default_base()
    detail = []
    for pad in sorted(pad_sounds, key=lambda p: p["pad_number"]):
        kind = classify(pad.get("name"), pad.get("folder"))
        note = pad.get("midi_note")
        index = (note_to_index or {}).get(note)
        if index is None and isinstance(note, int) and BANK_A_FIRST_NOTE <= note < BANK_A_FIRST_NOTE + 16:
            index = BANK_A_FIRST_INDEX + (note - BANK_A_FIRST_NOTE)
        if index is not None:
            colors[index] = _COLORS[kind]
        detail.append({"pad_number": pad["pad_number"], "name": pad.get("name"), "type": kind, "label": _LABELS[kind], "color": _COLORS[kind], "index": index})
    return colors, detail

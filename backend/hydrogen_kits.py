"""Imports Hydrogen drumkits (drumkit.xml + audio files) as DiakoPad kits.

Ubuntu Studio ships several free acoustic kits with Hydrogen
(/usr/share/hydrogen/data/drumkits, package hydrogen-drumkits): The Black
Pearl, Forzee Stereo, GM Rock (5 velocity layers each), BJA Pacific (8), the
"Gimme A Hand" percussion kit, etc. Kits the user downloads from Hydrogen's
own repository land in ~/.local/share/hydrogen/data/drumkits.

A DiakoPad pad plays one sample, so for each Hydrogen instrument the layer
covering LAYER_VELOCITY (a firm hit, 0..1) is used. Instruments are placed
on the 16 pads by role, following the DiakoPad layout (1 kick, 2 snare,
4/5 hi-hat, 11-13 toms, 14 crash, 15 ride...), then any instruments left
fill the empty pads in kit order. The kit's author and license are kept in
the DiakoPad kit's source_path (attribution).
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import storage

SAMPLES_DIR = Path(__file__).parent / "samples"
KIT_DIRS = [
    Path(p) for p in os.environ.get(
        "DIAKOPAD_HYDROGEN_KIT_DIRS",
        os.pathsep.join([
            "/usr/share/hydrogen/data/drumkits",
            str(Path.home() / ".local/share/hydrogen/data/drumkits"),
            str(Path.home() / ".hydrogen/data/drumkits"),
        ]),
    ).split(os.pathsep)
]
LAYER_VELOCITY = 0.8
CATEGORY = "05_hydrogen"

# (pad, role pattern) in priority order. Each instrument is used once.
ROLES: list[tuple[int, str]] = [
    (1, r"\bkick\b|bass ?drum|\bbd\b|cajon thump"),
    (2, r"^(?!.*(rim|roll|stick|2\b)).*(snare|cajon slap)"),
    (4, r"(hat|hh).*(clos)|clos.*(hat|hh)"),
    (5, r"^(?!.*semi)(.*(hat|hh).*open|.*open.*(hat|hh))"),
    (3, r"side ?stick|\bstick\b|rim ?click|clap"),
    (6, r"rimshot|snare.*rim|snare ?2|snare (rock|dry|jazz)"),
    (7, r"(hat|hh).*(pedal|semi|swish|free)|pedal.*(hat|hh)"),
    (11, r"tom.*(hi|high)|high.*tom|\btom ?1\b|^tom$"),
    (12, r"tom.*mid|mid.*tom|\btom ?2\b"),
    (13, r"floor|tom.*low|low.*tom"),
    (14, r"crash(?!.*(ride|choke|flink))"),
    (15, r"ride(?!.*(bell|cup))"),
    (16, r"(?<!cow)bell|cup"),
    (8, r"splash(?!.*choke)|china"),
    (9, r"tamb|maraca|shaker"),
    (10, r"cowbell|agogo|snap|clave|block"),
]


def _strip(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child(e: ET.Element, name: str) -> Optional[ET.Element]:
    return next((c for c in e if _strip(c.tag) == name), None)


def _children(e: Optional[ET.Element], name: str) -> list[ET.Element]:
    return [] if e is None else [c for c in e if _strip(c.tag) == name]


def _text(e: Optional[ET.Element], name: str, default: str = "") -> str:
    node = None if e is None else _child(e, name)
    return (node.text or "").strip() if node is not None and node.text else default


def _layers(inst: ET.Element) -> list[ET.Element]:
    layers = []
    for comp in _children(inst, "instrumentComponent") or [inst]:
        layers += _children(comp, "layer")
    return layers


def _pick_layer(layers: list[ET.Element]) -> Optional[ET.Element]:
    def rng(layer):
        try:
            return float(_text(layer, "min", "0")), float(_text(layer, "max", "1"))
        except ValueError:
            return 0.0, 1.0
    for layer in layers:
        lo, hi = rng(layer)
        if lo <= LAYER_VELOCITY <= hi:
            return layer
    return max(layers, key=lambda layer: rng(layer)[1], default=None)


def _kit_dir(name: str) -> Optional[Path]:
    for base in KIT_DIRS:
        path = base / name
        if (path / "drumkit.xml").is_file():
            return path
    return None


def parse_kit(path: Path) -> dict:
    root = ET.parse(path / "drumkit.xml").getroot()
    instruments = []
    for inst in _children(_child(root, "instrumentList"), "instrument"):
        layer = _pick_layer(_layers(inst))
        if layer is None:
            continue
        filename = _text(layer, "filename")
        audio = path / filename
        if not filename or not audio.is_file():
            continue
        instruments.append({"name": _text(inst, "name", audio.stem), "file": audio})
    return {
        "name": _text(root, "name", path.name),
        "author": _text(root, "author"),
        "license": _text(root, "license"),
        "instruments": instruments,
    }


def list_kits() -> list[dict]:
    """Hydrogen kits installed on this machine that have playable audio."""
    seen, result = set(), []
    for base in KIT_DIRS:
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.name in seen or not (path / "drumkit.xml").is_file():
                continue
            try:
                kit = parse_kit(path)
            except ET.ParseError:
                continue
            if not kit["instruments"]:
                continue
            seen.add(path.name)
            result.append({
                "name": path.name,
                "title": kit["name"],
                "author": kit["author"],
                "license": kit["license"] or "não informada no drumkit.xml",
                "instruments": len(kit["instruments"]),
            })
    return result


def assign_pads(instruments: list[dict]) -> dict[int, dict]:
    """{pad_number: instrument} following the DiakoPad role layout."""
    pads: dict[int, dict] = {}
    used: set[int] = set()
    for pad, pattern in ROLES:
        if pad in pads:
            continue
        rx = re.compile(pattern, re.IGNORECASE)
        for index, inst in enumerate(instruments):
            if index not in used and rx.search(inst["name"]):
                pads[pad] = inst
                used.add(index)
                break
    leftovers = [inst for index, inst in enumerate(instruments) if index not in used]
    for pad in range(1, 17):
        if pad not in pads and leftovers:
            pads[pad] = leftovers.pop(0)
    return pads


def _short(kit_title: str) -> str:
    return re.sub(r"\s*\d+(\.\d+)*$", "", kit_title).replace("_", " ").strip()


def import_kit(name: str) -> int:
    """Copies the chosen layer of each instrument into the library (folder
    Hydrogen/<kit>, reusing samples already imported) and creates/updates
    the DiakoPad kit. Returns its id."""
    path = _kit_dir(name)
    if path is None:
        raise FileNotFoundError(f"kit do Hydrogen não encontrado: {name}")
    kit = parse_kit(path)
    if not kit["instruments"]:
        raise ValueError(f"kit sem áudio utilizável: {name}")
    folder = storage.normalize_folder(f"Hydrogen/{_short(kit['name']).replace('/', '-')}")
    existing = {(s["folder"], s["display_name"]): s["id"] for s in storage.list_samples()}
    SAMPLES_DIR.mkdir(exist_ok=True)
    short = _short(kit["name"])
    pads = []
    for pad_number, inst in sorted(assign_pads(kit["instruments"]).items()):
        display = f"{short} {inst['name']}"[:80]
        sample_id = existing.get((folder, display))
        if sample_id is None:
            ext = inst["file"].suffix.lower()
            if ext not in storage.ALLOWED_SAMPLE_EXTENSIONS:
                continue
            safe = storage.SAFE_NAME_RE.sub("_", display) or "sample"
            stored = f"{safe}-{uuid.uuid4().hex[:8]}{ext}"
            shutil.copyfile(inst["file"], SAMPLES_DIR / stored)
            sample_id = storage.add_sample(stored, display, folder)
        pads.append({"pad_number": pad_number, "sample_id": sample_id, "display_name": inst["name"]})
    credit = f"Hydrogen: {kit['name']}"
    if kit["author"]:
        credit += f" — {kit['author']}"
    if kit["license"]:
        credit += f" ({kit['license'][:60]})"
    return storage.create_kit(f"{short} (Hydrogen)", CATEGORY, 0, pads, source_path=credit)

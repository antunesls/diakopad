#!/usr/bin/env python3
"""Curates a handful of source sample folders (e.g. a vintage drum-machine
sample archive) into DiakoPad kits (see storage.py's `kits`/`kit_pads`) and
packages them into a single importable `.zip` — a "kit pack".

Two-step, offline/occasional operation, same philosophy as import_tool.py.
Run with the venv active, from backend/:

    python curate_kit_pack.py scan "C:\\path\\to\\drum_machine" ..\\kit_pack_staging
    # -> writes kit_pack_staging/<slug>/samples/*.wav + kit_pack_staging/manifest.json

    # (optional) hand-edit kit_pack_staging/manifest.json - the automatic
    # role-per-sample guess ("role" field) is a review hint, not ground
    # truth, especially for messily-named source folders.

    python curate_kit_pack.py package ..\\kit_pack_staging ..\\kit_pack_v1.zip
    # -> zips the staging folder as-is into a single pack file

The pack is then imported into DiakoPad with import_kit_tool.py (or the
POST /api/kits/import upload endpoint), see kit_import.py.

Which source folders get scanned, and what category each lands in, is a
fixed CATEGORY_MAP below rather than "every folder in the archive" - a large
raw sample archive (hundreds of folders, heavy duplication/junk) needs a
human picking out the good ones first; this script curates a chosen subset,
it doesn't attempt full-archive automatic cleanup.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import storage

# Source folder name (exactly as it exists on disk under the archive root) ->
# category prefix. The numeric prefix drives category ordering in the kit
# browser (see storage.list_kit_categories); pick new prefixes in the gaps
# (e.g. "01_electronic", "02_sampler", "03_custom") to slot a new group in
# later without renumbering existing ones.
CATEGORY_MAP: dict[str, str] = {
    "Roland TR-808": "01_electronic",
    "Roland TR-909": "01_electronic",
    "Roland TR-707": "01_electronic",
    "Roland TR-606": "01_electronic",
    "Linn LinnDrum": "01_electronic",
    "Simmons SDS5": "01_electronic",
    "Boss DR-550": "01_electronic",
    "Alesis HR-16": "01_electronic",
    "Korg M1": "01_electronic",
    "Oberheim DMX": "01_electronic",
    "Akai MPC-60": "02_sampler",
    "Akai MPC3000": "02_sampler",
    "Emu SP12": "02_sampler",
    "Fairlight IIX": "02_sampler",
    "Diakonia": "03_custom",
    "machine kit": "03_custom",
}

# Short tag used in generated display names ("808 Kick", "SDS5 Caixa", ...).
KIT_TAG: dict[str, str] = {
    "Roland TR-808": "808",
    "Roland TR-909": "909",
    "Roland TR-707": "707",
    "Roland TR-606": "606",
    "Linn LinnDrum": "LinnDrum",
    "Simmons SDS5": "SDS5",
    "Boss DR-550": "DR550",
    "Alesis HR-16": "HR16",
    "Korg M1": "M1",
    "Oberheim DMX": "DMX",
    "Akai MPC-60": "MPC60",
    "Akai MPC3000": "MPC3000",
    "Emu SP12": "SP12",
    "Fairlight IIX": "Fairlight",
    "Diakonia": "Diakonia",
    "machine kit": "Machine",
}

# Junk we never want to copy/scan, regardless of extension.
_JUNK_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
_JUNK_DIR_RE = re.compile(r"^__.*__$")

# Ordered role -> keyword list. Keywords are matched against a *normalized*
# form of the filename stem + immediate parent folder name (lowercased, every
# non-alphanumeric character stripped) - this sidesteps the archive's wildly
# inconsistent separators ("Hat_C01", "Hat C 01", "HHCLOSE1", "Hat Closed-01"
# all normalize to something containing "hatc"/"hhclose"/"hatclosed"). Dict
# order fixes each role's pad_number (1-based, role's position in this dict)
# when it has a match; unmatched pad_numbers are filled from the fallback
# pool afterwards (see assign_roles).
ROLE_KEYWORDS: dict[str, list[str]] = {
    "kick": ["kick", "bdrum", "bassdrum", "bumbo"],
    "snare": ["snare", "snaredrum"],
    "clap": ["clap"],
    "closed_hat": ["hatc", "hhclose", "hatclosed", "closedhat", "hihat", "chh"],
    "open_hat": ["hato", "hhopen", "hatopen", "openhat", "ohh"],
    "rim": ["rim", "sidestick", "rimshot"],
    "cowbell": ["cowbell", "cowbel", "cow", "agogo"],
    "clave": ["clave", "claves"],
    "conga": ["conga"],
    "bongo": ["bongo"],
    "tom_low": ["tomlow", "toml"],
    "tom_mid": ["tommid", "tommed", "tomm"],
    "tom_high": ["tomhi", "tomhigh"],
    "cymbal": ["ride", "crash", "cymbal", "splash"],
    "perc": [
        "shaker", "tamborine", "tambourine", "perc", "ratchet", "timbale",
        "maracas", "cabasa", "woodblock", "triangle",
    ],
}

ROLE_DISPLAY_PT: dict[str, str] = {
    "kick": "Kick",
    "snare": "Caixa",
    "clap": "Palma",
    "closed_hat": "Chimbal Fechado",
    "open_hat": "Chimbal Aberto",
    "rim": "Rim Shot",
    "cowbell": "Agogô",
    "clave": "Clave",
    "conga": "Conga",
    "bongo": "Bongô",
    "tom_low": "Tom Grave",
    "tom_mid": "Tom Médio",
    "tom_high": "Tom Agudo",
    "cymbal": "Prato",
    "perc": "Percussão",
}

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _numeric_suffix(stem: str) -> int:
    """Ascending numeric suffix found in a filename stem, for tie-breaking
    between several files that match the same role (Kick01 before Kick05).
    Files with no trailing digits sort after ones that have them."""
    match = re.search(r"(\d+)$", stem)
    return int(match.group(1)) if match else 10**9


def score_role(filename: str, parent_folder: str = "") -> str | None:
    """Returns the ROLE_KEYWORDS key that best matches this file, or None if
    nothing matched. Pure function, no disk access - easy to unit test."""
    stem = Path(filename).stem
    haystack = _normalize(f"{parent_folder} {stem}")
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return role
    return None


def _is_junk(path: Path) -> bool:
    if path.name.lower() in _JUNK_NAMES:
        return True
    return any(_JUNK_DIR_RE.match(part) for part in path.parts)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "kit"


def _collect_candidate_files(source_dir: Path) -> list[Path]:
    files = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or _is_junk(path):
            continue
        if path.suffix.lower() not in storage.ALLOWED_SAMPLE_EXTENSIONS:
            continue
        files.append(path)
    return files


def assign_roles(files: list[Path]) -> dict[int, tuple[Path, str | None]]:
    """Picks one representative file per role (smallest numeric suffix wins
    a tie) and returns {pad_number: (file, role_or_None)} for every pad slot
    that ended up filled - either a matched role, or a fallback (role=None)
    filling a still-empty pad_number, up to 16 total. Files that matched a
    role but lost the tie-break are dropped (that role's sound already has a
    representative; DiakoPad kits are one sample per pad, not a pile of
    variations)."""
    by_role: dict[str, list[Path]] = {}
    unmatched: list[Path] = []
    for f in files:
        role = score_role(f.name, f.parent.name)
        if role is None:
            unmatched.append(f)
        else:
            by_role.setdefault(role, []).append(f)

    role_order = list(ROLE_KEYWORDS.keys())
    slots: dict[int, tuple[Path, str | None]] = {}
    for pad_number, role in enumerate(role_order, start=1):
        candidates = by_role.get(role)
        if not candidates:
            continue
        winner = min(candidates, key=lambda p: (_numeric_suffix(p.stem), p.name))
        slots[pad_number] = (winner, role)
        # Every other file that matched this role (lost the tie-break) joins
        # the fallback pool too, sorted alphabetically with the rest below.
        unmatched.extend(p for p in candidates if p != winner)

    fallback_pool = sorted(unmatched, key=lambda p: p.name.lower())
    next_pad = 1
    for f in fallback_pool:
        while next_pad in slots:
            next_pad += 1
        if next_pad > 16:
            break
        slots[next_pad] = (f, None)
        next_pad += 1

    return slots


def _display_name(kit_tag: str, role: str | None, filename: str) -> str:
    if role is not None:
        return f"{kit_tag} {ROLE_DISPLAY_PT[role]}"
    stem = re.sub(r"\s+", " ", Path(filename).stem.replace("_", " ").replace("-", " ")).strip()
    # Fallback (unrecognized-role) samples: most source filenames already
    # embed the machine name/tag ("TR-808Clap02", "SD" for Simmons SDS...) -
    # only prepend the kit tag when the filename doesn't already read as one
    # of its own, to avoid "808 TR 808 Clap02"-style duplication.
    if _normalize(kit_tag) in _normalize(stem):
        return stem
    return f"{kit_tag} {stem}".strip()


def scan(source_root: Path, output_dir: Path) -> None:
    if not source_root.is_dir():
        print(f"não é uma pasta: {source_root}")
        raise SystemExit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_kits = []
    summary_lines = []

    for source_name, category in CATEGORY_MAP.items():
        source_dir = source_root / source_name
        if not source_dir.is_dir():
            print(f"AVISO: pasta não encontrada, pulando: {source_name}")
            continue

        files = _collect_candidate_files(source_dir)
        slots_by_pad = assign_roles(files)
        kit_tag = KIT_TAG.get(source_name, source_name)
        slug = _slugify(source_name)
        dest_samples_dir = output_dir / slug / "samples"
        dest_samples_dir.mkdir(parents=True, exist_ok=True)

        pads = []
        for pad_number in sorted(slots_by_pad):
            src_file, role = slots_by_pad[pad_number]
            dest_file = dest_samples_dir / src_file.name
            shutil.copyfile(src_file, dest_file)
            display_name = _display_name(kit_tag, role, src_file.name)
            pads.append(
                {
                    "pad_number": pad_number,
                    "role": role,
                    "filename": f"{slug}/samples/{src_file.name}",
                    "display_name": display_name,
                    "source_file": str(src_file.relative_to(source_root)).replace("\\", "/"),
                }
            )

        manifest_kits.append(
            {
                "name": source_name,
                "category": category,
                "sort_index": 0,  # filled below, sequential within category
                "source_path": source_name,
                "pads": pads,
            }
        )
        flag = " ⚠ revisar (poucos papéis reconhecidos)" if len(pads) < 4 else ""
        summary_lines.append(f"  {source_name}: {len(pads)} pads preenchidos{flag}")

    # sort_index: sequential within each category, in CATEGORY_MAP order.
    counters: dict[str, int] = {}
    for kit in manifest_kits:
        cat = kit["category"]
        counters[cat] = counters.get(cat, 0) + 10
        kit["sort_index"] = counters[cat]

    manifest = {
        "pack_name": "Vintage Drum Machines - Curated v1",
        "created_at": datetime.now(timezone.utc).date().isoformat(),
        "kits": manifest_kits,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Escaneados {len(manifest_kits)} kits de {len(CATEGORY_MAP)} pastas configuradas:")
    print("\n".join(summary_lines))
    print(f"\nRevise {manifest_path} antes de empacotar (o campo \"role\" é só uma dica).")


def package(staging_dir: Path, pack_path: Path) -> None:
    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"manifest.json não encontrado em {staging_dir} - rode 'scan' primeiro")
        raise SystemExit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    pack_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pack_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest_path, "manifest.json")
        for kit in manifest["kits"]:
            for pad in kit["pads"]:
                rel = pad["filename"]
                zf.write(staging_dir / rel, rel)

    kit_count = len(manifest["kits"])
    pad_count = sum(len(k["pads"]) for k in manifest["kits"])
    print(f"Empacotado {pack_path}: {kit_count} kits, {pad_count} samples")


def main(argv: list[str]) -> None:
    if len(argv) != 3 or argv[0] not in ("scan", "package"):
        print("uso:")
        print("  python curate_kit_pack.py scan <pasta_de_origem> <pasta_de_saida>")
        print("  python curate_kit_pack.py package <pasta_de_saida> <arquivo.zip>")
        raise SystemExit(1)

    command, arg1, arg2 = argv
    if command == "scan":
        scan(Path(arg1), Path(arg2))
    else:
        package(Path(arg1), Path(arg2))


if __name__ == "__main__":
    main(sys.argv[1:])

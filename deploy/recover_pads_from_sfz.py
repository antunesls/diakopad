#!/usr/bin/env python3
"""Recovers the pad assignments lost when a deploy overwrote diakopad.db.

Sources of truth (both survived the overwrite):
  * backend/samples/*.wav - the sample files themselves (flat, uuid-suffixed)
  * the sfz bank's padNN.sfz - written by apply_pad for every assigned pad,
    containing the sample path, midi key, volume, pan and (if set) cutoff.

Rebuilds the samples catalog and the pads table. Effect chains, knob
mappings, sequencer steps and settings are NOT recoverable from these
artifacts and keep their defaults.

Run from the repository with the service stopped.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import storage

# Defaults to the local SFZ bank. Override with DIAKOPAD_SFZ_BANK_DIR when
# the live application uses another location.
BANK_DIR = Path(os.environ.get(
    "DIAKOPAD_SFZ_BANK_DIR",
    Path(__file__).resolve().parent.parent / "backend" / "sfz_bank",
))
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "backend" / "samples"
SUFFIX_RE = re.compile(r"-[0-9a-f]{8}$")


def rebuild_samples_catalog() -> dict[str, int]:
    by_filename = {s["filename"]: s["id"] for s in storage.list_samples()}
    for path in sorted(SAMPLES_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in storage.ALLOWED_SAMPLE_EXTENSIONS:
            continue
        if path.name in by_filename:
            continue
        display = SUFFIX_RE.sub("", path.stem)
        by_filename[path.name] = storage.add_sample(path.name, display, "")
        print(f"sample catalog: + {path.name} -> {display!r}")
    return by_filename


def parse_sfz(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    region = next((line for line in text.splitlines() if line.startswith("<region>")), None)
    if region is None:
        return None
    values: dict[str, str] = {}
    for token in region.split()[1:]:
        if "=" in token:
            key, _, value = token.partition("=")
            values[key] = value
    sample = values.get("sample")
    if not sample:
        return None
    return {
        "sample_filename": Path(sample).name,
        "midi_note": int(values.get("key", "36")),
        "volume_db": float(values.get("volume", "6.0")),
        "pan": float(values.get("pan", "0.0")),
        "cutoff_hz": float(values["cutoff"]) if "cutoff" in values else "__unset__",
    }


def main() -> int:
    if not BANK_DIR.is_dir():
        print(f"bank dir not found: {BANK_DIR}", file=sys.stderr)
        return 1
    by_filename = rebuild_samples_catalog()
    restored = 0
    for sfz in sorted(BANK_DIR.glob("pad??.sfz")):
        match = re.match(r"pad(\d{2})\.sfz$", sfz.name)
        if not match:
            continue
        pad_number = int(match.group(1))
        data = parse_sfz(sfz)
        if data is None:
            continue
        sample_id = by_filename.get(data["sample_filename"])
        if sample_id is None:
            print(f"pad{pad_number:02d}: sample file missing for {data['sample_filename']}", file=sys.stderr)
            continue
        storage.assign_sample(pad_number, sample_id)
        storage.set_pad_note(pad_number, data["midi_note"])
        storage.set_pad_mix(pad_number, volume_db=data["volume_db"], pan=data["pan"])
        if data["cutoff_hz"] != "__unset__":
            storage.set_pad_mix(pad_number, cutoff_hz=data["cutoff_hz"])
        restored += 1
        print(
            f"pad{pad_number:02d}: {data['sample_filename']} "
            f"(nota {data['midi_note']}, vol {data['volume_db']}, pan {data['pan']})"
        )
    print(f"restored {restored} pads, {len(by_filename)} samples in catalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

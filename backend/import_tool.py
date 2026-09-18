#!/usr/bin/env python3
"""Bulk-imports every audio file found under a source directory into
DiakoPad's sample library, preserving its folder structure as browsable
metadata (samples.folder) - not on disk: the actual files always land flat
in backend/samples/ under their own uuid-suffixed name (same convention as
a manual upload, see app.py's upload_sound), which sidesteps path-length
issues when the source tree is deeply nested.

Occasional/offline operation, not a live app feature - run it directly,
with the venv active, from backend/:

    python import_tool.py "C:\\path\\to\\sample\\library"

Safe to re-run on the same source: a (folder, display_name) pair already in
the DB is skipped rather than duplicated, so adding a few new packs to an
already-imported library only imports what's new.
"""
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import storage

SAMPLES_DIR = Path(__file__).parent / "samples"


def import_folder(source: Path) -> None:
    if not source.is_dir():
        print(f"não é uma pasta: {source}")
        raise SystemExit(1)

    storage.init_db()
    SAMPLES_DIR.mkdir(exist_ok=True)
    existing = {(s["folder"], s["display_name"]) for s in storage.list_samples()}

    imported = skipped_existing = skipped_type = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in storage.ALLOWED_SAMPLE_EXTENSIONS:
            skipped_type += 1
            continue

        rel_folder = path.parent.relative_to(source).as_posix()
        if rel_folder == ".":
            rel_folder = ""
        display_name = path.stem

        if (rel_folder, display_name) in existing:
            skipped_existing += 1
            continue

        safe_stem = storage.SAFE_NAME_RE.sub("_", display_name) or "sample"
        stored_filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{ext}"
        shutil.copyfile(path, SAMPLES_DIR / stored_filename)

        storage.add_sample(stored_filename, display_name, rel_folder)
        existing.add((rel_folder, display_name))
        imported += 1
        if imported % 200 == 0:
            print(f"... {imported} importados")

    print(
        f"Concluído: {imported} importados, {skipped_existing} já existiam, "
        f"{skipped_type} ignorados (não são áudio)"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python import_tool.py <pasta de origem>")
        raise SystemExit(1)
    import_folder(Path(sys.argv[1]))

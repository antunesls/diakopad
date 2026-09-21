"""Imports a kit pack (see curate_kit_pack.py) into DiakoPad: extracts the
zip's manifest.json + referenced samples, adds each sample to the library
(same uuid-suffixed-filename convention as import_tool.py/app.py's
upload_sound) and creates one storage.kits row per manifest kit.

Upserts by kit name (storage.create_kit already does this), so re-running
after editing manifest.json - or after regenerating the pack - is safe."""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import storage

SAMPLES_DIR = Path(__file__).parent / "samples"


def import_pack(zip_path: Path) -> list[int]:
    storage.init_db()
    SAMPLES_DIR.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)

        manifest_path = tmp_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("pacote inválido: manifest.json não encontrado no zip")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        kit_ids = []
        for kit in manifest.get("kits", []):
            pads = []
            for pad in kit.get("pads", []):
                rel = pad["filename"]
                src = tmp_dir / rel
                if not src.is_file():
                    raise ValueError(f"pacote inválido: arquivo referenciado ausente: {rel}")
                ext = src.suffix.lower()
                if ext not in storage.ALLOWED_SAMPLE_EXTENSIONS:
                    raise ValueError(f"tipo de arquivo não suportado: {rel}")

                display_name = pad.get("display_name") or src.stem
                safe_stem = storage.SAFE_NAME_RE.sub("_", display_name) or "sample"
                stored_filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{ext}"
                shutil.copyfile(src, SAMPLES_DIR / stored_filename)

                folder = kit.get("name", "")
                sample_id = storage.add_sample(stored_filename, display_name, storage.normalize_folder(_safe_folder(folder)))
                pads.append(
                    {
                        "pad_number": pad["pad_number"],
                        "sample_id": sample_id,
                        "display_name": display_name,
                    }
                )

            kit_id = storage.create_kit(
                name=kit["name"],
                category=kit.get("category", ""),
                sort_index=kit.get("sort_index", 0),
                pads=pads,
                source_path=kit.get("source_path"),
            )
            kit_ids.append(kit_id)

        return kit_ids


def _safe_folder(name: str) -> str:
    """Kit names may contain characters normalize_folder rejects as path
    separators (e.g. none expected in practice, but be defensive) - fold
    anything odd into a flat logical folder name instead of raising."""
    return name.replace("/", "-").replace("\\", "-")

#!/usr/bin/env python3
"""Imports a kit pack (.zip, see curate_kit_pack.py) into DiakoPad.

Occasional/offline operation, not a live app feature - run it directly,
with the venv active, from backend/:

    python import_kit_tool.py path\\to\\pack.zip

Safe to re-run: kits are upserted by name (see kit_import.import_pack)."""
from __future__ import annotations

import sys
from pathlib import Path

from kit_import import import_pack

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python import_kit_tool.py <pacote.zip>")
        raise SystemExit(1)
    ids = import_pack(Path(sys.argv[1]))
    print(f"Concluído: {len(ids)} kits importados/atualizados (ids: {ids})")

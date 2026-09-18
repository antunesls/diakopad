"""Per-pad sfizz JACK-client process lifecycle.

One `sfizz_jack` subprocess per pad, each loading only that pad's own tiny
.sfz and exposing its own JACK client (`diakopad_padNN`, ports `output_1`/
`output_2` and MIDI input `input` - see sfizz's clients/jack_client.cpp).
`--jack_autoconnect=false` because DiakoPad wires the graph itself
(backend/engine/jackgraph.py) instead of letting sfizz auto-patch to
physical outputs.

Best-effort: if the sfizz_jack binary isn't on PATH (e.g. local dev), spawn
calls are logged once and skipped, same fallback spirit as midi.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("diakopad.engine.sfizz_proc")

SFIZZ_BIN = os.environ.get("DIAKOPAD_SFIZZ_BIN", "sfizz_jack")

_procs: dict[int, subprocess.Popen] = {}
_watchdog_task: Optional[asyncio.Task] = None
_unavailable_logged = False


def client_name(pad_number: int) -> str:
    return f"diakopad_pad{pad_number:02d}"


def _binary_available() -> bool:
    global _unavailable_logged
    found = shutil.which(SFIZZ_BIN) is not None
    if not found and not _unavailable_logged:
        logger.warning("%r not found on PATH; sfizz playback is disabled", SFIZZ_BIN)
        _unavailable_logged = True
    return found


def spawn(pad_number: int, sfz_path: Path) -> bool:
    """(Re)starts the sfizz instance for a pad, replacing any previous one."""
    stop(pad_number)
    if not _binary_available():
        return False
    try:
        proc = subprocess.Popen(
            [
                SFIZZ_BIN,
                f"--client_name={client_name(pad_number)}",
                "--jack_autoconnect=false",
                str(sfz_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("failed to spawn sfizz for pad %d: %s", pad_number, exc)
        return False
    _procs[pad_number] = proc
    logger.info(
        "spawned sfizz for pad %d (pid %d, client %s)", pad_number, proc.pid, client_name(pad_number)
    )
    return True


def stop(pad_number: int) -> None:
    proc = _procs.pop(pad_number, None)
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def is_running(pad_number: int) -> bool:
    proc = _procs.get(pad_number)
    return proc is not None and proc.poll() is None


def stop_all() -> None:
    for pad_number in list(_procs):
        stop(pad_number)


async def _watchdog(interval: float = 3.0) -> None:
    while True:
        await asyncio.sleep(interval)
        for pad_number, proc in list(_procs.items()):
            if proc.poll() is not None:
                logger.warning(
                    "sfizz for pad %d exited unexpectedly (code %s); leaving it stopped "
                    "until the pad is reassigned or a mix value changes",
                    pad_number,
                    proc.returncode,
                )
                _procs.pop(pad_number, None)


def start_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is None:
        _watchdog_task = asyncio.create_task(_watchdog())

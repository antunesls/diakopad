"""Generic sfizz JACK-client process lifecycle, keyed by JACK client name.

One `sfizz_jack` subprocess per client, each loading its own tiny .sfz and
exposing its own JACK ports (`output_1`/`output_2` and MIDI input `input` -
see sfizz's clients/jack_client.cpp). Used both for the 16 performance pads
(`client_name(pad_number)` -> `diakopad_padNN`) and for the dedicated
metronome click instance (`diakopad_metronome`, see
engine/metronome_sounds.py) - anything that needs its own independent
one-shot-sample player gets its own client here.

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

_procs: dict[str, subprocess.Popen] = {}
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


def spawn(client: str, sfz_path: Path) -> bool:
    """(Re)starts the sfizz instance for a client, replacing any previous one."""
    stop(client)
    if not _binary_available():
        return False
    try:
        proc = subprocess.Popen(
            [
                SFIZZ_BIN,
                f"--client_name={client}",
                "--jack_autoconnect=false",
                str(sfz_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("failed to spawn sfizz for %s: %s", client, exc)
        return False
    _procs[client] = proc
    logger.info("spawned sfizz for %s (pid %d)", client, proc.pid)
    return True


def stop(client: str) -> None:
    proc = _procs.pop(client, None)
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def is_running(client: str) -> bool:
    proc = _procs.get(client)
    return proc is not None and proc.poll() is None


def stop_all() -> None:
    for client in list(_procs):
        stop(client)


async def _watchdog(interval: float = 3.0) -> None:
    while True:
        await asyncio.sleep(interval)
        for client, proc in list(_procs.items()):
            if proc.poll() is not None:
                logger.warning(
                    "sfizz for %s exited unexpectedly (code %s); leaving it stopped "
                    "until reassigned/reapplied",
                    client,
                    proc.returncode,
                )
                _procs.pop(client, None)


def start_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is None:
        _watchdog_task = asyncio.create_task(_watchdog())

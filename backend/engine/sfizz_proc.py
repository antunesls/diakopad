"""Generic sfizz JACK-client process lifecycle, keyed by JACK client name.

One `sfizz_jack` subprocess per client, each loading its own tiny .sfz and
exposing its own JACK ports (`output_1`/`output_2` and MIDI input `input` -
see sfizz's clients/jack_client.cpp). Used both for the 16 performance pads
(`client_name(pad_number, slot)` -> `diakopad_padNNa`/`diakopad_padNNb` - see
engine/orchestrator.py's double-buffered swap, which keeps a pad's currently
live slot playing until its replacement's ports are confirmed up) and for the
dedicated metronome click instance (`diakopad_metronome`, see
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
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("diakopad.engine.sfizz_proc")

SFIZZ_BIN = os.environ.get("DIAKOPAD_SFIZZ_BIN", "sfizz_jack")

_procs: dict[str, subprocess.Popen] = {}
_sfz_paths: dict[str, Path] = {}
_watchdog_task: Optional[asyncio.Task] = None
_unavailable_logged = False
_recovery_handler: Optional[Callable[[str], Awaitable[bool]]] = None
_recovery_attempts: dict[str, int] = {}
_next_recovery_at: dict[str, float] = {}

MAX_RECOVERY_ATTEMPTS = 3
MAX_RECOVERY_BACKOFF_SECONDS = 30.0


def client_name(pad_number: int, slot: str = "a") -> str:
    return f"diakopad_pad{pad_number:02d}{slot}"


def _binary_available() -> bool:
    global _unavailable_logged
    found = shutil.which(SFIZZ_BIN) is not None
    if not found and not _unavailable_logged:
        logger.warning("%r not found on PATH; sfizz playback is disabled", SFIZZ_BIN)
        _unavailable_logged = True
    return found


def spawn(client: str, sfz_path: Path) -> bool:
    """(Re)starts the sfizz instance for a client, replacing any previous one."""
    stop(client, forget=False)
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
    _sfz_paths[client] = sfz_path
    logger.info("spawned sfizz for %s (pid %d)", client, proc.pid)
    return True


def stop(client: str, forget: bool = True, graceful: bool = False) -> None:
    """SIGKILL by default: sfizz_jack has no state to flush on exit, and
    measured on real hardware, a graceful SIGTERM shutdown took ~1s per
    instance against ~15ms for JACK to notice a killed client and free its
    ports - spawn() immediately re-registers the same client_name right
    after, so that port teardown latency is what actually gated a respawn.

    `graceful=True` is for the one caller that isn't time-critical: tearing
    down a pad's old slot after orchestrator.py's double-buffered swap has
    already moved the live audio graph onto the new one. Giving sfizz_jack a
    real chance to shut down cleanly there, instead of piling on more
    concurrent SIGKILLs, is believed to ease pressure on the PipeWire/JACK
    bridge (observed to occasionally crash under bursts of killed clients)."""
    proc = _procs.pop(client, None)
    if forget:
        _sfz_paths.pop(client, None)
        _recovery_attempts.pop(client, None)
        _next_recovery_at.pop(client, None)
    if proc is None:
        return
    if graceful:
        proc.terminate()
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            logger.warning("graceful stop of %s timed out after 3s; escalating to SIGKILL", client)
    proc.kill()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def is_running(client: str) -> bool:
    proc = _procs.get(client)
    return proc is not None and proc.poll() is None


def stop_all() -> None:
    for client in set(_procs) | set(_sfz_paths):
        stop(client)


def emergency_stop_all() -> None:
    """Immediately terminates every player without waiting for process exit.

    Players stay tracked so the watchdog can rebuild them after a PANIC
    fallback when the optional master-gain LV2 plugin is unavailable.
    """
    for proc in list(_procs.values()):
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


def schedule_recovery(client: str) -> None:
    """Keeps a player in the retry queue when its JACK ports never appear."""
    _next_recovery_at.setdefault(client, time.monotonic())


def mark_recovered(client: str) -> None:
    _recovery_attempts.pop(client, None)
    _next_recovery_at.pop(client, None)


def set_recovery_handler(handler: Callable[[str], Awaitable[bool]]) -> None:
    global _recovery_handler
    _recovery_handler = handler


async def _watchdog(interval: float = 3.0) -> None:
    while True:
        await asyncio.sleep(interval)
        now = asyncio.get_running_loop().time()
        for client, proc in list(_procs.items()):
            if proc.poll() is not None:
                logger.warning(
                    "sfizz for %s exited unexpectedly (code %s); scheduling recovery",
                    client,
                    proc.returncode,
                )
                _procs.pop(client, None)
                _next_recovery_at[client] = now

        for client, retry_at in list(_next_recovery_at.items()):
            if retry_at > now:
                continue
            attempts = _recovery_attempts.get(client, 0)
            if _recovery_handler is None:
                logger.error("sfizz recovery unavailable for %s", client)
                _next_recovery_at.pop(client, None)
                continue
            if attempts >= MAX_RECOVERY_ATTEMPTS:
                logger.error("sfizz recovery paused for %s; retrying in %.0f seconds", client, MAX_RECOVERY_BACKOFF_SECONDS)
                _recovery_attempts[client] = 0
                _next_recovery_at[client] = now + MAX_RECOVERY_BACKOFF_SECONDS
                continue
            _recovery_attempts[client] = attempts + 1
            try:
                recovered = await _recovery_handler(client)
            except Exception as exc:  # pragma: no cover - defensive recovery path
                logger.exception("sfizz recovery failed for %s: %s", client, exc)
                recovered = False
            if recovered:
                _recovery_attempts.pop(client, None)
                _next_recovery_at.pop(client, None)
                continue
            _next_recovery_at[client] = now + min(2 ** (attempts + 1), MAX_RECOVERY_BACKOFF_SECONDS)


def start_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is None:
        _watchdog_task = asyncio.create_task(_watchdog())


async def stop_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None

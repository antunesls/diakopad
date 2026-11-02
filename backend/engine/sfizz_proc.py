"""Generic sfizz JACK-client process lifecycle, keyed by a logical client name.

One `sfizz_jack` subprocess per logical client (the 16 pads, the metronome,
the kit-browse preview), each loading its own .sfz and exposing JACK ports
(`output_1`/`output_2` and MIDI input `input` - see sfizz's
clients/jack_client.cpp). `--jack_autoconnect=false` because DiakoPad wires
the graph itself (backend/engine/jackgraph.py).

Pads use *generations* (prepare -> commit -> retire) so a sound change never
leaves a gap, the opposite of loading a kit with the audio blocked
(Hydrogen's setSong does that under the engine lock):

* prepare(): starts the next generation under its own JACK name
  (`diakopad_pad03_7`) while the current one keeps playing.
* commit(): the new generation becomes current; the old one goes to the
  retired list ("death row", as Hydrogen does with instruments that still
  have playing notes) and is only killed after SWAP_GRACE_SECONDS, so a
  cymbal that was ringing when the scene changed rings out.
* reap_retired(): run by the watchdog; kills retired generations whose time
  is up and deletes their private bank file.

Metronome and preview keep the plain spawn() (stop, then start) - they have
no tails worth keeping.

Samples are preloaded entirely into RAM (`--preload_size`, Hydrogen keeps
whole samples in memory too); sfizz's default (8192 frames, ~0.19 s) streams
anything longer from disk while it plays.

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
# frames; 4M frames = ~95 s at 44.1 kHz, i.e. every drum sample fully in RAM.
PRELOAD_SIZE = int(os.environ.get("DIAKOPAD_SFIZZ_PRELOAD_SIZE", "4194304"))
# note_polyphony=4 per region already caps a pad; 16 leaves room for the
# release of the previous variant after a scene CC.
NUM_VOICES = int(os.environ.get("DIAKOPAD_SFIZZ_VOICES", "16"))
SWAP_GRACE_SECONDS = float(os.environ.get("DIAKOPAD_SWAP_GRACE_SECONDS", "10"))

_procs: dict[str, subprocess.Popen] = {}
_sfz_paths: dict[str, Path] = {}
_jack_names: dict[str, str] = {}
_generation: dict[str, int] = {}
_prepared: dict[str, dict] = {}
_retired: list[dict] = []
_watchdog_task: Optional[asyncio.Task] = None
_unavailable_logged = False
_recovery_handler: Optional[Callable[[str], Awaitable[bool]]] = None
_recovery_attempts: dict[str, int] = {}
_next_recovery_at: dict[str, float] = {}

MAX_RECOVERY_ATTEMPTS = 3
MAX_RECOVERY_BACKOFF_SECONDS = 30.0


def client_name(pad_number: int) -> str:
    return f"diakopad_pad{pad_number:02d}"


def jack_name(client: str) -> str:
    """JACK client name of the generation currently playing for `client`."""
    return _jack_names.get(client, client)


def _binary_available() -> bool:
    global _unavailable_logged
    found = shutil.which(SFIZZ_BIN) is not None
    if not found and not _unavailable_logged:
        logger.warning("%r not found on PATH; sfizz playback is disabled", SFIZZ_BIN)
        _unavailable_logged = True
    return found


def _popen(name: str, sfz_path: Path) -> Optional[subprocess.Popen]:
    try:
        return subprocess.Popen(
            [
                SFIZZ_BIN,
                f"--client_name={name}",
                "--jack_autoconnect=false",
                f"--preload_size={PRELOAD_SIZE}",
                f"--num_voices={NUM_VOICES}",
                str(sfz_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("failed to spawn sfizz %s: %s", name, exc)
        return None


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


def _is_private_bank(path: Optional[Path]) -> bool:
    return path is not None and path.parent.name == "live"


def spawn(client: str, sfz_path: Path) -> bool:
    """(Re)starts the instance for a client in place (stop, then start)."""
    stop(client, forget=False)
    if not _binary_available():
        return False
    proc = _popen(client, sfz_path)
    if proc is None:
        return False
    _procs[client] = proc
    _sfz_paths[client] = sfz_path
    _jack_names[client] = client
    logger.info("spawned sfizz for %s (pid %d)", client, proc.pid)
    return True


def next_tag(client: str) -> str:
    """Generation tag the next prepare() will use (also names its bank file)."""
    return str(_generation.get(client, 0) + 1)


def prepare(client: str, sfz_path: Path) -> Optional[str]:
    """Starts the next generation alongside the current one. Returns its
    JACK client name, or None if sfizz can't be started."""
    abort(client)
    if not _binary_available():
        return None
    gen = _generation.get(client, 0) + 1
    _generation[client] = gen
    name = f"{client}_{gen}"
    proc = _popen(name, sfz_path)
    if proc is None:
        return None
    _prepared[client] = {"proc": proc, "name": name, "sfz": sfz_path}
    logger.info("prepared sfizz %s (pid %d)", name, proc.pid)
    return name


def abort(client: str) -> None:
    prep = _prepared.pop(client, None)
    if prep is not None:
        _kill(prep["proc"])
        if _is_private_bank(prep["sfz"]):
            prep["sfz"].unlink(missing_ok=True)


def _retire_current(client: str, grace: float) -> None:
    old = _procs.pop(client, None)
    if old is not None and old.poll() is None:
        _retired.append({
            "client": client, "proc": old, "name": jack_name(client),
            "deadline": time.monotonic() + grace, "sfz": _sfz_paths.get(client),
        })
    elif _is_private_bank(_sfz_paths.get(client)):
        _sfz_paths[client].unlink(missing_ok=True)


def commit(client: str, grace: float = SWAP_GRACE_SECONDS) -> bool:
    """Makes the prepared generation current; the previous one is retired
    (kept alive for `grace` seconds so its notes ring out)."""
    prep = _prepared.pop(client, None)
    if prep is None:
        return False
    _retire_current(client, grace)
    _procs[client] = prep["proc"]
    _jack_names[client] = prep["name"]
    _sfz_paths[client] = prep["sfz"]
    return True


def retire(client: str, grace: float = SWAP_GRACE_SECONDS) -> None:
    """Current generation goes to death row and nothing replaces it (pad
    emptied): it gets no new notes but its last one rings out."""
    abort(client)
    _retire_current(client, grace)
    _sfz_paths.pop(client, None)
    _jack_names.pop(client, None)
    _recovery_attempts.pop(client, None)
    _next_recovery_at.pop(client, None)


def retired_names(client: str) -> list[str]:
    return [r["name"] for r in _retired if r["client"] == client and r["proc"].poll() is None]


def reap_retired(now: Optional[float] = None) -> int:
    """Kills retired generations whose grace period is over. Returns how many
    were removed from the list."""
    moment = time.monotonic() if now is None else now
    keep, removed = [], 0
    for r in _retired:
        if r["proc"].poll() is None and r["deadline"] > moment:
            keep.append(r)
            continue
        _kill(r["proc"])
        if _is_private_bank(r["sfz"]):
            r["sfz"].unlink(missing_ok=True)
        removed += 1
    _retired[:] = keep
    return removed


def stop(client: str, forget: bool = True) -> None:
    """Immediately stops every generation of a client (current, prepared and
    retired) - used for PANIC-like paths, the metronome and the preview."""
    abort(client)
    for r in [r for r in _retired if r["client"] == client]:
        _kill(r["proc"])
        _retired.remove(r)
    proc = _procs.pop(client, None)
    if forget:
        _sfz_paths.pop(client, None)
        _jack_names.pop(client, None)
        _recovery_attempts.pop(client, None)
        _next_recovery_at.pop(client, None)
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
    for client in set(_procs) | set(_sfz_paths) | set(_prepared) | {r["client"] for r in _retired}:
        stop(client)


def emergency_stop_all() -> None:
    """Immediately terminates every player without waiting for process exit.

    Current players stay tracked so the watchdog can rebuild them after a
    PANIC fallback when the optional master-gain LV2 plugin is unavailable.
    Prepared and retired generations are dropped for good.
    """
    for proc in list(_procs.values()):
        _kill(proc)
    for client in list(_prepared):
        abort(client)
    for r in _retired:
        _kill(r["proc"])
    _retired.clear()


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
        reap_retired()
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

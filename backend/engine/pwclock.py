"""Keeps PipeWire's graph clock pinned to DiakoPad's low-latency setting.

install-ubuntu-studio.sh writes the wanted rate/quantum to
~/.config/pipewire/pipewire.conf.d/99-diakopad-clock.conf, but that only sets
the *defaults*. Ubuntu Studio's audio widget (and any app that asks for it)
writes `clock.force-rate` / `clock.force-quantum` into the "settings"
metadata at runtime, which override those defaults - in the field that left
the laptop at 48000 Hz / 1024 frames (~21 ms) instead of 44100 / 128 (~3 ms).

So DiakoPad forces the clock itself: once at startup (before mod-host and
sfizz are spawned, so every plugin instantiates at the right rate) and then
from the engine supervisor, which puts it back if something changes it.

Everything here is a no-op when `pw-metadata` is not available (jackd2,
development machines).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("diakopad.engine.pwclock")

CLOCK_CONF = Path(
    os.environ.get(
        "DIAKOPAD_CLOCK_CONF",
        Path.home() / ".config/pipewire/pipewire.conf.d/99-diakopad-clock.conf",
    )
)
DEFAULT_RATE = 44100
DEFAULT_QUANTUM = 128
_TIMEOUT = 5
# If something keeps moving the sample rate back, restoring it (and the
# engine restart that implies) would loop forever. After this many rate
# restores within the window, stop fighting and report it instead.
MAX_RATE_RESTORES = 3
RATE_RESTORE_WINDOW_S = 300.0
_rate_restores: list[float] = []
gave_up = False


def _conf_value(text: str, key: str) -> Optional[int]:
    match = re.search(rf"^\s*default\.clock\.{re.escape(key)}\s*=\s*(\d+)", text, re.MULTILINE)
    return int(match.group(1)) if match else None


def wanted_clock() -> Optional[tuple[int, int]]:
    """(rate, quantum) DiakoPad should run at, or None to leave the clock
    alone. DIAKOPAD_CLOCK_RATE / DIAKOPAD_CLOCK_QUANTUM win; otherwise the
    values come from the clock conf the installer wrote. DIAKOPAD_CLOCK=off
    disables enforcement."""
    if os.environ.get("DIAKOPAD_CLOCK", "").lower() in ("off", "0", "false"):
        return None
    text = CLOCK_CONF.read_text() if CLOCK_CONF.is_file() else ""
    rate = os.environ.get("DIAKOPAD_CLOCK_RATE") or _conf_value(text, "rate")
    quantum = os.environ.get("DIAKOPAD_CLOCK_QUANTUM") or _conf_value(text, "quantum")
    if rate is None and quantum is None and not text:
        return None  # not installed by install-ubuntu-studio.sh: don't touch the user's audio
    return int(rate or DEFAULT_RATE), int(quantum or DEFAULT_QUANTUM)


def available() -> bool:
    return shutil.which("pw-metadata") is not None


def _run(*args: str) -> Optional[str]:
    try:
        res = subprocess.run(
            ["pw-metadata", "-n", "settings", *args],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("pw-metadata failed: %s", exc)
        return None
    return res.stdout


def current_forced() -> dict[str, int]:
    """Current clock.force-rate / clock.force-quantum (0 = not forced)."""
    out = _run("0") or ""
    values = {}
    for key in ("clock.force-rate", "clock.force-quantum"):
        match = re.search(rf"key:'{re.escape(key)}' value:'(\d+)'", out)
        values[key] = int(match.group(1)) if match else 0
    return values


def enforce() -> dict:
    """Forces the wanted clock if it isn't already in place.

    Returns {"changed": bool, "rate_changed": bool, "before": {...}}. A rate
    change means already-running LV2 plugins/sfizz are now at the wrong rate,
    so the caller should restart the engine."""
    result = {"changed": False, "rate_changed": False, "before": {}}
    wanted = wanted_clock()
    if wanted is None or not available():
        return result
    global gave_up
    if gave_up:
        return result
    rate, quantum = wanted
    before = current_forced()
    if before.get("clock.force-rate") != rate:
        now = time.monotonic()
        _rate_restores[:] = [t for t in _rate_restores if now - t < RATE_RESTORE_WINDOW_S] + [now]
        if len(_rate_restores) > MAX_RATE_RESTORES:
            gave_up = True
            logger.error(
                "PipeWire sample rate keeps being changed to %s by another program; "
                "no longer enforcing %d Hz (check Ubuntu Studio's audio widget)",
                before.get("clock.force-rate"), rate,
            )
            return result
    result["before"] = before
    if before.get("clock.force-rate") != rate:
        _run("0", "clock.force-rate", str(rate))
        result["changed"] = result["rate_changed"] = True
    if before.get("clock.force-quantum") != quantum:
        _run("0", "clock.force-quantum", str(quantum))
        result["changed"] = True
    if result["changed"]:
        logger.warning(
            "PipeWire clock was forced to %s Hz / %s frames; restored %d Hz / %d frames",
            before.get("clock.force-rate") or "default", before.get("clock.force-quantum") or "default",
            rate, quantum,
        )
    return result

"""Client for mod-host's line-based TCP control protocol
(https://github.com/moddevices/mod-host - `add "<uri>" <n>`, `connect
"<src>" "<dst>"`, `param_set <n> "<symbol>" <value>`, `remove <n>`, each
answered with a line `resp <status> [value]`, status >= 0 meaning success).

This is the same LV2 plugin host Zynthian itself uses for effect chains
(reverb, delay, ...); DiakoPad drives it directly instead of going through
zyngine/MOD-UI, hosting the per-pad serial effect chains and the optional
master-gain instance that mutes every route during PANIC.

Best-effort like the rest of backend/engine: if the `mod-host` binary or its
control socket isn't reachable (e.g. local dev), calls are logged once and
swallowed instead of crashing the app.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import time
from typing import Optional

logger = logging.getLogger("diakopad.engine.modhost")

MODHOST_BIN = os.environ.get("DIAKOPAD_MODHOST_BIN", "mod-host")
CONTROL_PORT = int(os.environ.get("DIAKOPAD_MODHOST_PORT", "5555"))
HOST = "127.0.0.1"
CONNECT_TIMEOUT_SECONDS = 5.0
RECV_BUFSIZE = 4096

_proc: Optional[subprocess.Popen] = None
_sock: Optional[socket.socket] = None
_lock = asyncio.Lock()
_unavailable_logged = False


def _log_unavailable(exc: Exception) -> None:
    global _unavailable_logged
    if not _unavailable_logged:
        logger.warning("mod-host unavailable (%s); LV2 effects are disabled", exc)
        _unavailable_logged = True


def start() -> bool:
    """Spawns the mod-host process (once) if the binary is on PATH. Only one
    process is needed - it hosts every plugin instance (buses + per-pad
    send-gains) over the same control socket."""
    global _proc
    if _proc is not None and _proc.poll() is None:
        return True
    if shutil.which(MODHOST_BIN) is None:
        _log_unavailable(FileNotFoundError(MODHOST_BIN))
        return False
    try:
        _proc = subprocess.Popen(
            [MODHOST_BIN, "-n", "-p", str(CONTROL_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        _log_unavailable(exc)
        return False
    logger.info("spawned mod-host (pid %d, control port %d)", _proc.pid, CONTROL_PORT)
    return True


def is_configured() -> bool:
    return shutil.which(MODHOST_BIN) is not None


def is_alive() -> bool:
    return _proc is not None and _proc.poll() is None


def restart() -> bool:
    stop()
    return start()


def _connect_socket(timeout: float = CONNECT_TIMEOUT_SECONDS) -> Optional[socket.socket]:
    global _sock
    if _sock is not None:
        return _sock
    deadline = time.monotonic() + timeout
    last_exc: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            _sock = socket.create_connection((HOST, CONTROL_PORT), timeout=2.0)
            return _sock
        except OSError as exc:
            last_exc = exc
            time.sleep(0.2)
    if last_exc is not None:
        _log_unavailable(last_exc)
    return None


def _send_command_sync(command: str) -> Optional[str]:
    global _sock
    sock = _connect_socket()
    if sock is None:
        return None
    try:
        sock.sendall((command + "\n").encode("utf-8"))
        data = sock.recv(RECV_BUFSIZE)
        return data.decode("utf-8", errors="replace").strip()
    except OSError as exc:
        logger.warning("mod-host socket error on %r: %s", command, exc)
        _sock = None
        return None


async def send_command(command: str) -> Optional[str]:
    """Sends one line of the mod-host protocol and returns the raw `resp
    ...` reply, or None if mod-host isn't reachable. Calls are serialized
    (one connection, one request in flight at a time) and run off the
    asyncio loop since the socket I/O is blocking."""
    async with _lock:
        return await asyncio.to_thread(_send_command_sync, command)


def _parse_status(reply: Optional[str]) -> Optional[int]:
    if reply is None or not reply.startswith("resp"):
        return None
    parts = reply.split()
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


async def add(lv2_uri: str, instance: int) -> bool:
    reply = await send_command(f'add "{lv2_uri}" {instance}')
    status = _parse_status(reply)
    ok = status is not None and status >= 0
    if not ok:
        logger.warning("mod-host add(%s, %d) failed: %r", lv2_uri, instance, reply)
    return ok


async def remove(instance: int) -> bool:
    status = _parse_status(await send_command(f"remove {instance}"))
    return status is not None and status >= 0


async def connect_ports(src: str, dst: str) -> bool:
    status = _parse_status(await send_command(f'connect "{src}" "{dst}"'))
    return status is not None and status >= 0


async def param_set(instance: int, symbol: str, value: float) -> bool:
    reply = await send_command(f'param_set {instance} "{symbol}" {value}')
    status = _parse_status(reply)
    ok = status is not None and status >= 0
    if not ok:
        logger.debug("mod-host param_set(%d, %s, %s) failed: %r", instance, symbol, value, reply)
    return ok


def stop() -> None:
    global _proc, _sock
    if _sock is not None:
        try:
            _sock.close()
        except OSError:
            pass
        _sock = None
    if _proc is not None:
        _proc.terminate()
        try:
            _proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _proc.kill()
        _proc = None

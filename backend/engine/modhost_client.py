"""Client for mod-host's line-based TCP control protocol
(https://github.com/moddevices/mod-host - `add "<uri>" <n>`, `connect
"<src>" "<dst>"`, `param_set <n> "<symbol>" <value>`, `remove <n>`, each
answered with a line `resp <status> [value]`, status >= 0 meaning success
(errors are negative, see mod-host src/effects.h; `add` replies with the
allocated instance number on success).

Protocol notes validated on-device against the Zynthian OS build
(see /zynthian/zynthian-sw/mod-host):

* Commands are NUL-terminated (``command\\x00``), NOT newline-terminated.
  With ``\\n`` the server's frame accounting in socket.c goes negative and
  its parser starts interpreting heap memory as commands (garbled replies
  and crashes). Zynthian's own clients (zyngine) use NUL framing.
* The binary daemonizes: the spawned parent exits with code 0 immediately
  and a detached child owns the control port. Liveness must therefore be
  probed over TCP (or via the ``quit`` command), never via the Popen handle.
* Effects live in their own JACK clients named ``effect_<instance>`` with
  ports named after the LV2 port symbols.

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


def _port_accepts(timeout: float = 0.5) -> bool:
    """True if a mod-host daemon is listening on the control port."""
    try:
        probe = socket.create_connection((HOST, CONTROL_PORT), timeout=timeout)
    except OSError:
        return False
    probe.close()
    return True


def start() -> bool:
    """Ensures a mod-host daemon is running: adopts an existing one (the
    daemon outlives app restarts), otherwise spawns it (once). Only one
    daemon is needed - it hosts every plugin instance (buses + per-pad
    send-gains) over the same control socket."""
    global _proc
    if _port_accepts():
        logger.info("adopted existing mod-host on control port %d", CONTROL_PORT)
        return True
    if shutil.which(MODHOST_BIN) is None:
        _log_unavailable(FileNotFoundError(MODHOST_BIN))
        return False
    try:
        _proc = subprocess.Popen(
            # No "-n" flag: the mod-host binary shipped with Zynthian OS
            # rejects it (usage: -v -i -p -f) and exits before opening the
            # control socket.
            [MODHOST_BIN, "-p", str(CONTROL_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        _log_unavailable(exc)
        return False
    logger.info("spawned mod-host (pid %d, control port %d)", _proc.pid, CONTROL_PORT)
    # The parent forks a daemon child and exits immediately; give the child
    # a moment to bind the control port before callers start connecting.
    opened = False
    for _ in range(25):
        if _port_accepts(0.2):
            opened = True
            break
        time.sleep(0.2)
    if not opened:
        logger.warning("mod-host daemon did not open control port %d in time", CONTROL_PORT)
    # Reap the short-lived forking parent so it doesn't sit as a zombie for
    # the rest of the daemon's (long) lifetime - it has already exited by
    # now, win or lose, since forking+exiting is what makes the port
    # available (or not) in the first place.
    try:
        _proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        logger.warning("mod-host spawn process (pid %d) did not exit as expected", _proc.pid)
    return opened


def is_configured() -> bool:
    return shutil.which(MODHOST_BIN) is not None


def is_alive() -> bool:
    """The daemon detaches from the Popen handle, so liveness is probed over
    TCP (or inferred from the persistent control socket being open)."""
    if _sock is not None:
        return True
    return _port_accepts()


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
        # NUL-terminated framing: a trailing "\n" corrupts the server's
        # frame accounting and its parser starts reading heap memory.
        sock.sendall((command + "\x00").encode("utf-8"))
        data = sock.recv(RECV_BUFSIZE)
        reply = data.decode("utf-8", errors="replace")
        return reply.split("\x00")[0].strip()
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
    # add replies with the allocated instance number on success (>= 0);
    # errors are negative (ERR_* enums in mod-host's src/effects.h).
    ok = status == instance
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
            # Graceful shutdown of the detached daemon over the protocol.
            _sock.sendall(b"quit\x00")
            _sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            _sock.close()
        except OSError:
            pass
        _sock = None
    if _proc is not None:
        if _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None

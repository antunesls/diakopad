"""Thin wrapper over the JACK graph - connecting/disconnecting ports for the
sfizz/mod-host orchestration. Replaces the old boot-time `jack_connect`
oneshot (deploy/diakopad-midi-connect.service): connections are now made and
remade dynamically as pads are (re)assigned, from inside the app itself.

Best-effort like the rest of backend/engine: if the `jack` package or the
JACK server isn't available (e.g. local Windows dev), calls are logged once
and swallowed instead of crashing the app - the same fallback spirit as
midi.py's virtual MIDI ports.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("diakopad.engine.jackgraph")

_client = None
_unavailable_logged = False


def _on_shutdown(*_args) -> None:
    global _client
    logger.warning("JACK shut down; the graph will be recreated when it returns")
    _client = None


def _get_client():
    global _client, _unavailable_logged
    if _client:
        return _client
    _client = None
    try:
        import jack

        _client = jack.Client("DiakoPad-orchestrator", no_start_server=True)
        _client.set_shutdown_callback(_on_shutdown)
    except Exception as exc:  # pragma: no cover - environment dependent
        if not _unavailable_logged:
            logger.warning("JACK unavailable (%s); audio graph wiring will be no-ops", exc)
            _unavailable_logged = True
        _client = False
    return _client


def available() -> bool:
    return bool(_get_client())


def has_port(name_pattern: str) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        return bool(client.get_ports(name_pattern))
    except Exception as exc:
        _on_shutdown(exc)
        return False


def wait_for_port(name_pattern: str, timeout: float = 5.0) -> bool:
    """Polls the JACK port list until a port matching name_pattern (passed
    straight to jack.Client.get_ports, which takes a regex) shows up, or the
    timeout elapses. Used right after spawning a pad's sfizz instance, since
    its JACK ports only exist once the process has finished initializing."""
    client = _get_client()
    if not client:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get_ports(name_pattern):
                return True
        except Exception as exc:
            _on_shutdown(exc)
            return False
        time.sleep(0.1)
    return False


async def wait_for_port_async(name_pattern: str, timeout: float = 5.0) -> bool:
    """Async equivalent of wait_for_port for request handlers and clocks.

    The JACK client call itself is quick; yielding between polls keeps the
    FastAPI event loop responsive while sfizz finishes registering its ports.
    """
    client = _get_client()
    if not client:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get_ports(name_pattern):
                return True
        except Exception as exc:
            _on_shutdown(exc)
            return False
        await asyncio.sleep(0.1)
    return False


def connect(src: str, dst: str) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        client.connect(src, dst)
        return True
    except Exception as exc:
        # Already-connected is the common case and not worth a warning.
        logger.debug("jack connect(%s, %s): %s", src, dst, exc)
        return False


def disconnect(src: str, dst: str) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        client.disconnect(src, dst)
        return True
    except Exception as exc:
        logger.debug("jack disconnect(%s, %s): %s", src, dst, exc)
        return False


def connect_pattern_to_all(src_pattern: str, dst_pattern: str) -> bool:
    """Connects every output port matching src_pattern to every input port
    matching dst_pattern (both regexes, as jack.Client.get_ports takes).
    Used to fan the SMC-PAD's raw hardware MIDI capture port(s) out to a
    destination without needing to know its exact port name in advance -
    the dynamic equivalent of the old boot-time shell loop in
    deploy/diakopad-midi-connect.service."""
    client = _get_client()
    if not client:
        return False
    try:
        srcs = client.get_ports(src_pattern, is_output=True)
        dsts = client.get_ports(dst_pattern, is_input=True)
    except Exception as exc:
        logger.debug("jack get_ports(%s)/(%s): %s", src_pattern, dst_pattern, exc)
        _on_shutdown(exc)
        return False
    connected = False
    for src in srcs:
        for dst in dsts:
            connected = connect(src.name, dst.name) or connected
    return connected


def disconnect_all(port_name_pattern: str) -> None:
    """Disconnects every connection touching ports matching the pattern -
    used before rewiring a pad whose sfizz instance was just respawned
    (its old ports are gone, so any stale connections need clearing)."""
    client = _get_client()
    if not client:
        return
    try:
        for port in client.get_ports(port_name_pattern):
            for other in client.get_all_connections(port):
                client.disconnect(port, other)
    except Exception as exc:
        logger.debug("jack disconnect_all(%s): %s", port_name_pattern, exc)
        _on_shutdown(exc)

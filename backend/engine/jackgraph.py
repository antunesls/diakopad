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

import logging
import time
from typing import Optional

logger = logging.getLogger("diakopad.engine.jackgraph")

_client = None
_unavailable_logged = False


def _get_client():
    global _client, _unavailable_logged
    if _client is not None:
        return _client
    try:
        import jack

        _client = jack.Client("DiakoPad-orchestrator", no_start_server=True)
    except Exception as exc:  # pragma: no cover - environment dependent
        if not _unavailable_logged:
            logger.warning("JACK unavailable (%s); audio graph wiring will be no-ops", exc)
            _unavailable_logged = True
        _client = False
    return _client


def available() -> bool:
    return bool(_get_client())


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
        if client.get_ports(name_pattern):
            return True
        time.sleep(0.1)
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


def connect_pattern_to_all(src_pattern: str, dst_pattern: str) -> None:
    """Connects every output port matching src_pattern to every input port
    matching dst_pattern (both regexes, as jack.Client.get_ports takes).
    Used to fan the SMC-PAD's raw hardware MIDI capture port(s) out to a
    destination without needing to know its exact port name in advance -
    the dynamic equivalent of the old boot-time shell loop in
    deploy/diakopad-midi-connect.service."""
    client = _get_client()
    if not client:
        return
    try:
        srcs = client.get_ports(src_pattern, is_output=True)
        dsts = client.get_ports(dst_pattern, is_input=True)
    except Exception as exc:
        logger.debug("jack get_ports(%s)/(%s): %s", src_pattern, dst_pattern, exc)
        return
    for src in srcs:
        for dst in dsts:
            connect(src.name, dst.name)


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

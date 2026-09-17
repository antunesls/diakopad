"""Sends the MIDI Program Change that makes Zynthian reload the active kit.

Uses mido/python-rtmidi to open (or create, on Linux with the rtmidi ALSA
backend) an output port. On the real deployment this port is connected once
to Zynthian's MIDI router input via `aconnect` (see deploy/install.sh) so it
does not need to be reconnected on every restart.

Kept best-effort: if no MIDI backend/port is available (e.g. local
development on a machine with no MIDI hardware), calls are logged and
swallowed instead of crashing the web app.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("diakopad.midi")

PORT_NAME = os.environ.get("DIAKOPAD_MIDI_PORT_NAME", "DiakoPad")
INPUT_PORT_NAME = os.environ.get("DIAKOPAD_MIDI_INPUT_PORT_NAME", "DiakoPad-in")
MIDI_CHANNEL = int(os.environ.get("DIAKOPAD_MIDI_CHANNEL", "10")) - 1  # 0-indexed

_port = None
_unavailable_logged = False
_input_port = None


def _get_port():
    global _port, _unavailable_logged
    if _port is not None:
        return _port
    try:
        import mido

        _port = mido.open_output(PORT_NAME, virtual=True)
        logger.info("Opened virtual MIDI output port %r", PORT_NAME)
    except Exception as exc:  # pragma: no cover - environment dependent
        if not _unavailable_logged:
            logger.warning("MIDI output unavailable (%s); Program Change calls will be no-ops", exc)
            _unavailable_logged = True
        _port = False
    return _port


def ensure_open() -> bool:
    """Opens the virtual MIDI port eagerly (called on app startup) so it's
    already visible to `jack_lsp`/`aconnect` for a boot-time auto-connect
    script, instead of only appearing after the first pad assignment."""
    return bool(_get_port())


def send_program_change(preset_index: int) -> bool:
    """Sends a Program Change on MIDI_CHANNEL. Returns True if actually sent."""
    port = _get_port()
    if not port:
        return False
    import mido

    port.send(mido.Message("program_change", program=preset_index, channel=MIDI_CHANNEL))
    logger.info("Sent Program Change %d on channel %d", preset_index, MIDI_CHANNEL + 1)
    return True


def open_input(on_cc: Callable[[int, int], None]) -> bool:
    """Opens a virtual MIDI input port and calls on_cc(control_number, value)
    for every Control Change received, on any channel. The callback fires on
    mido/rtmidi's own thread, not the asyncio loop - callers that touch
    asyncio state must hop back with loop.call_soon_threadsafe.

    Connect the SMC-PAD's knobs to this port once, on the real device, via
    `jack_connect` from its raw hardware capture port (see
    deploy/diakopad-midi-connect.service) - it's separate from the ZynMidi
    Router-bound output port above.
    """
    global _input_port
    try:
        import mido

        def _callback(msg) -> None:
            if msg.type == "control_change":
                on_cc(msg.control, msg.value)

        _input_port = mido.open_input(INPUT_PORT_NAME, virtual=True, callback=_callback)
        logger.info("Opened virtual MIDI input port %r", INPUT_PORT_NAME)
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("MIDI input unavailable (%s); knob learning will not work", exc)
        return False

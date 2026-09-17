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

logger = logging.getLogger("diakopad.midi")

PORT_NAME = os.environ.get("DIAKOPAD_MIDI_PORT_NAME", "DiakoPad")
MIDI_CHANNEL = int(os.environ.get("DIAKOPAD_MIDI_CHANNEL", "10")) - 1  # 0-indexed

_port = None
_unavailable_logged = False


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


def send_program_change(preset_index: int) -> bool:
    """Sends a Program Change on MIDI_CHANNEL. Returns True if actually sent."""
    port = _get_port()
    if not port:
        return False
    import mido

    port.send(mido.Message("program_change", program=preset_index, channel=MIDI_CHANNEL))
    logger.info("Sent Program Change %d on channel %d", preset_index, MIDI_CHANNEL + 1)
    return True

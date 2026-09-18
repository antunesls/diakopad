"""MIDI I/O not already covered by the per-pad sfizz instances themselves.

Two virtual ports, both best-effort (logged and swallowed, never crashing
the app, if no MIDI backend/port is available - e.g. local dev on a machine
with no MIDI hardware):

- An INPUT port (`DiakoPad-in`) fed by the SMC-PAD's hardware knobs/pads
  (see engine/orchestrator.py's hardware MIDI fan-out): Control Change for
  knob-learn, and now also Note On, observed (not acted on) so the live
  looper (engine/looper.py) can record what's actually being played.
- An OUTPUT port (`DiakoPad-trigger-out`) DiakoPad uses to trigger pads
  programmatically - the step sequencer and the looper's own playback both
  send Note On/Off through here, fanned out by the orchestrator to every
  pad's own sfizz `:input` (see engine/trigger.py). This port must never be
  wired back into `DiakoPad-in`, or the looper would record its own
  sequencer/loop-triggered hits.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("diakopad.midi")

INPUT_PORT_NAME = os.environ.get("DIAKOPAD_MIDI_INPUT_PORT_NAME", "DiakoPad-in")
OUTPUT_PORT_NAME = os.environ.get("DIAKOPAD_MIDI_OUTPUT_PORT_NAME", "DiakoPad-trigger-out")
MIDI_CHANNEL = int(os.environ.get("DIAKOPAD_MIDI_CHANNEL", "10")) - 1  # 0-indexed

_input_port = None
_output_port = None
_output_unavailable_logged = False


def open_input(on_cc: Callable[[int, int], None], on_note: Optional[Callable[[int, int], None]] = None) -> bool:
    """Opens the virtual MIDI input port. on_cc(control, value) fires for
    every Control Change; on_note(note, velocity), if given, fires for every
    Note On (velocity 0 note-ons - the common "note off" encoding - are not
    forwarded to it). Both fire on mido/rtmidi's own thread, not the asyncio
    loop - callers that touch asyncio state must hop back with
    loop.call_soon_threadsafe."""
    global _input_port
    try:
        import mido

        def _callback(msg) -> None:
            if msg.type == "control_change":
                on_cc(msg.control, msg.value)
            elif msg.type == "note_on" and msg.velocity > 0 and on_note is not None:
                on_note(msg.note, msg.velocity)

        _input_port = mido.open_input(INPUT_PORT_NAME, virtual=True, callback=_callback)
        logger.info("Opened virtual MIDI input port %r", INPUT_PORT_NAME)
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("MIDI input unavailable (%s); knob learning/loop recording will not work", exc)
        return False


def open_output() -> bool:
    """Opens the virtual MIDI output port used to trigger pads
    programmatically (see engine/trigger.py)."""
    global _output_port, _output_unavailable_logged
    try:
        import mido

        _output_port = mido.open_output(OUTPUT_PORT_NAME, virtual=True)
        logger.info("Opened virtual MIDI output port %r", OUTPUT_PORT_NAME)
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        if not _output_unavailable_logged:
            logger.warning("MIDI trigger output unavailable (%s); sequencer/looper playback will be no-ops", exc)
            _output_unavailable_logged = True
        return False


def note_on(channel: int, note: int, velocity: int = 100) -> bool:
    if _output_port is None:
        return False
    import mido

    _output_port.send(mido.Message("note_on", channel=channel, note=note, velocity=velocity))
    return True


def note_off(channel: int, note: int) -> bool:
    if _output_port is None:
        return False
    import mido

    _output_port.send(mido.Message("note_off", channel=channel, note=note, velocity=0))
    return True

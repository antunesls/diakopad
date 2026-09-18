"""Single primitive for programmatically "playing" a pad, shared by the step
sequencer (engine/sequencer.py) and the live looper (engine/looper.py) so
neither duplicates MIDI-sending logic.

Sends over midi.py's DiakoPad-trigger-out virtual port, which
orchestrator.py fans out to every pad's own sfizz `:input` JACK port -
never to DiakoPad-in, so these triggers are never mistaken for live hits by
the looper's recorder.
"""
from __future__ import annotations

import asyncio

import midi


def _note_for_pad(pad_number: int, pads: list[dict]) -> int | None:
    pad = next((p for p in pads if p["pad_number"] == pad_number), None)
    return pad["midi_note"] if pad else None


async def trigger_pad(
    pad_number: int,
    pads: list[dict],
    velocity: int = 100,
    settings: dict | None = None,
) -> bool:
    """Sends a Note On for this pad, plus a delayed Note Off unless
    sustain_mode is on (the default - one-shot samples ignore note-off
    anyway, see sfz.py, so sending it would just be wasted MIDI traffic)."""
    note = _note_for_pad(pad_number, pads)
    if note is None:
        return False
    sent = midi.note_on(midi.MIDI_CHANNEL, note, velocity)
    if sent and settings is not None and settings.get("sustain_mode", "1") != "1":
        asyncio.get_event_loop().call_later(0.05, midi.note_off, midi.MIDI_CHANNEL, note)
    return sent

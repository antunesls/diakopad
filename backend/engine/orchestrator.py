"""Central audio-engine orchestrator.

Owns the per-pad sfizz instances, the shared mod-host process (per-pad
effect-slot chains, see effects_catalog.py), the sequencer/looper trigger
port, and wires everything together over JACK. DiakoPad owns every audio
process directly - a pad reload is just "respawn this one instance", and an
effect param change is just a mod-host param_set with no restart at all.

JACK topology per pad:
  SMC-PAD hardware ------------------> diakopad_padNN:input (MIDI; each
  DiakoPad-trigger-out (sequencer/     pad's .sfz only reacts to its own
    looper, see engine/trigger.py) --> key=note, so no filtering needed)
  diakopad_padNN:output_1/2 --> slot1 (if any) --> slot2 (if any) -->
                                 slot3 (if any) --> system:playback
  (skips straight to system:playback when every slot is empty)

Every public function is best-effort: on a machine without JACK/sfizz_jack/
mod-host (e.g. local Windows dev), calls degrade to logging and returning
False - the same fallback spirit as midi.py's virtual-port fallback.

Plugin URIs and port/parameter symbol names (effects_catalog.py) are
environment-configured because they're specific to whatever LV2 plugins are
actually installed on the target Zynthian OS image - they must be
confirmed with `lv2info <uri>` on the real device (see deploy/install.sh)
rather than assumed here.
"""
from __future__ import annotations

import logging
import os
import re

import midi
import sfz
from engine import effects_catalog, jackgraph, metronome_sounds, modhost_client, sfizz_proc

logger = logging.getLogger("diakopad.engine.orchestrator")

MASTER_L = os.environ.get("DIAKOPAD_JACK_MASTER_L", "system:playback_1")
MASTER_R = os.environ.get("DIAKOPAD_JACK_MASTER_R", "system:playback_2")

# The SMC-PAD's raw hardware MIDI capture port(s), as they show up in
# `jack_lsp` on this Zynthian install (see the old
# deploy/diakopad-midi-connect.service, now superseded by the dynamic
# wiring below). Fans out to DiakoPad-in (knob learn + live-hit observing
# for the looper) and to every pad's own sfizz instance (each pad's tiny
# .sfz only reacts to its own `key=note`).
HARDWARE_MIDI_PATTERN = os.environ.get("DIAKOPAD_HARDWARE_MIDI_PATTERN", "system:midi_capture_.*")

# instance numbering scheme: pad N's slot S lives at mod-host instance
# 1000 + N*10 + S - arbitrary but fixed, with headroom for 16 pads x 3 slots.
_SLOT_INSTANCE_BASE = 1000

# (pad_number, slot_index) -> plugin_id currently live in mod-host for that
# slot, so apply_pad_effects only remove+add when the plugin actually
# changed (a param-only change is much cheaper, see set_effect_param).
_live_slot_plugin: dict[tuple[int, int], str | None] = {}


def _slot_instance(pad_number: int, slot_index: int) -> int:
    return _SLOT_INSTANCE_BASE + pad_number * 10 + slot_index


def _effect_client(instance: int) -> str:
    return f"effect_{instance}"


async def startup() -> None:
    """Called once from the FastAPI startup hook: spawns mod-host, opens the
    trigger-out port, and starts the sfizz watchdog. Individual pads are
    brought up afterwards via apply_all_pads() with the current DB state."""
    modhost_client.start()
    midi.open_output()
    jackgraph.connect_pattern_to_all(HARDWARE_MIDI_PATTERN, rf".*:{re.escape(midi.INPUT_PORT_NAME)}$")
    sfizz_proc.start_watchdog()


async def apply_pad(pad_number: int, pads: list[dict], settings: dict, pad_effects: list[dict]) -> bool:
    """Rerenders one pad's .sfz, (re)spawns its sfizz instance, and rewires
    its MIDI inputs + effect chain. Called after a sample/note/volume/pan/
    tone change (anything that needs the sfizz process itself restarted).
    Returns True if the real engine actually picked it up (false on dev
    machines without sfizz)."""
    pad = next((p for p in pads if p["pad_number"] == pad_number), None)
    if pad is None:
        return False
    client = sfizz_proc.client_name(pad_number)
    if not pad.get("filename"):
        sfizz_proc.stop(client)
        return False

    sfz_path = sfz.write_pad_kit(pad_number, pad, settings)
    if not sfizz_proc.spawn(client, sfz_path):
        return False

    if not jackgraph.wait_for_port(f"{client}:output_1"):
        logger.warning("sfizz JACK ports for pad %d never appeared", pad_number)
        return False

    jackgraph.connect_pattern_to_all(HARDWARE_MIDI_PATTERN, f"^{re.escape(client)}:input$")
    jackgraph.connect_pattern_to_all(rf"{re.escape(midi.OUTPUT_PORT_NAME)}$", f"^{re.escape(client)}:input$")
    await apply_pad_effects(pad_number, pad_effects)
    return True


async def apply_all_pads(pads: list[dict], settings: dict, pad_effects: list[dict]) -> None:
    """Used after a global settings change (sustain/velocity) since that
    affects every pad's rendered .sfz, not just one."""
    for pad in pads:
        if pad.get("filename"):
            await apply_pad(pad["pad_number"], pads, settings, pad_effects)


async def apply_pad_effects(pad_number: int, pad_effects: list[dict]) -> None:
    """(Re)creates/removes mod-host instances for this pad's slots as needed
    and rebuilds its serial chain. Called both from apply_pad() (after a
    respawn, since the pad's JACK ports are new) and directly when only a
    slot's plugin assignment changes (no sfizz restart needed)."""
    slots = sorted(
        (row for row in pad_effects if row["pad_number"] == pad_number),
        key=lambda r: r["slot_index"],
    )
    for row in slots:
        key = (pad_number, row["slot_index"])
        instance = _slot_instance(pad_number, row["slot_index"])
        live_plugin = _live_slot_plugin.get(key)
        plugin_id = row["plugin_id"]
        if plugin_id != live_plugin:
            if live_plugin is not None:
                await modhost_client.remove(instance)
            loaded = False
            if plugin_id is not None:
                plugin = effects_catalog.PLUGIN_CATALOG.get(plugin_id)
                if plugin and plugin["lv2_uri"]:
                    loaded = await modhost_client.add(plugin["lv2_uri"], instance)
                else:
                    logger.warning("no LV2 URI configured for effect %r; leaving pad %d slot %d empty", plugin_id, pad_number, row["slot_index"])
            _live_slot_plugin[key] = plugin_id if loaded else None
        if _live_slot_plugin.get(key):
            for symbol, value in row["params"].items():
                await modhost_client.param_set(instance, symbol, value)

    _rewire_pad_chain(pad_number, slots)


def _rewire_pad_chain(pad_number: int, slots: list[dict]) -> None:
    client = sfizz_proc.client_name(pad_number)
    if not jackgraph.available():
        return

    jackgraph.disconnect_all(rf"^{re.escape(client)}:output_.$")
    prev_out = (f"{client}:output_1", f"{client}:output_2")
    for row in slots:
        plugin_id = _live_slot_plugin.get((pad_number, row["slot_index"]))
        if not plugin_id:
            continue
        plugin = effects_catalog.PLUGIN_CATALOG[plugin_id]
        instance = _slot_instance(pad_number, row["slot_index"])
        slot_client = _effect_client(instance)
        jackgraph.disconnect_all(rf"^{re.escape(slot_client)}:")
        in_ports, out_ports = plugin["in_ports"], plugin["out_ports"]
        jackgraph.connect(prev_out[0], f"{slot_client}:{in_ports[0]}")
        jackgraph.connect(prev_out[1], f"{slot_client}:{in_ports[1]}")
        prev_out = (f"{slot_client}:{out_ports[0]}", f"{slot_client}:{out_ports[1]}")

    jackgraph.connect(prev_out[0], MASTER_L)
    jackgraph.connect(prev_out[1], MASTER_R)


async def set_effect_param(pad_number: int, slot_index: int, symbol: str, value: float) -> bool:
    """Cheap path for an effect slider change: no process restart, no
    rewiring, just a mod-host param_set."""
    instance = _slot_instance(pad_number, slot_index)
    return await modhost_client.param_set(instance, symbol, value)


METRONOME_CLIENT = "diakopad_metronome"


async def apply_metronome_style(style: str) -> bool:
    """(Re)spawns the dedicated metronome sfizz instance with the chosen
    click style and wires it into the graph - same pattern as a pad, but it
    isn't one of the 16 performance pads and only ever plays the two fixed
    click notes (see engine/metronome_sounds.py)."""
    sfz_path = metronome_sounds.write_metronome_sfz(style)
    if not sfizz_proc.spawn(METRONOME_CLIENT, sfz_path):
        return False
    if not jackgraph.wait_for_port(f"{METRONOME_CLIENT}:output_1"):
        logger.warning("sfizz JACK ports for the metronome never appeared")
        return False
    jackgraph.connect(f"{METRONOME_CLIENT}:output_1", MASTER_L)
    jackgraph.connect(f"{METRONOME_CLIENT}:output_2", MASTER_R)
    jackgraph.connect_pattern_to_all(
        rf"{re.escape(midi.OUTPUT_PORT_NAME)}$", f"^{re.escape(METRONOME_CLIENT)}:input$"
    )
    return True


def shutdown() -> None:
    sfizz_proc.stop_all()
    modhost_client.stop()

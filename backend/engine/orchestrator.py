"""Central audio-engine orchestrator.

Owns the per-pad sfizz instances, the shared mod-host process (per-pad
effect-slot chains, see effects_catalog.py), the sequencer/looper trigger
port, and wires everything together over JACK. DiakoPad owns every audio
process directly - a pad reload is just "respawn this one instance", and an
effect param change is just a mod-host param_set with no restart at all.

JACK topology per pad:
  SMC-PAD hardware -> DiakoPad-in -> DiakoPad-hardware-out -> diakopad_padNN:input
  DiakoPad-trigger-out (sequencer/looper, see engine/trigger.py) --> same inputs
  diakopad_padNN:output_1/2 --> slot1 (if any) --> slot2 (if any) -->
                                 slot3 (if any) --> system:playback
  (skips straight to system:playback when every slot is empty)

Every public function is best-effort: on a machine without JACK/sfizz_jack/
mod-host (e.g. local Windows dev), calls degrade to logging and returning
False - the same fallback spirit as midi.py's virtual-port fallback.

Plugin URIs and port/parameter symbol names (effects_catalog.py) are
environment-configured because they depend on the LV2 plugins installed on
the presentation laptop. Confirm them with `lv2info <uri>` when changing the
Ubuntu Studio audio setup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import midi
import sfz
import storage
import system_metrics
from engine import effects_catalog, jackgraph, looper, metronome, metronome_sounds, modhost_client, sequencer, sfizz_proc

logger = logging.getLogger("diakopad.engine.orchestrator")

MASTER_L = os.environ.get("DIAKOPAD_JACK_MASTER_L", "system:playback_1")
MASTER_R = os.environ.get("DIAKOPAD_JACK_MASTER_R", "system:playback_2")

# The SMC-PAD's PipeWire MIDI bridge port fans out to DiakoPad-in (knob learn
# and live-hit observing for the looper) and to every sfizz pad instance.
# Override this when the controller exposes a different JACK-compatible name.
HARDWARE_MIDI_PATTERN = os.environ.get(
    "DIAKOPAD_HARDWARE_MIDI_PATTERN", "Midi-Bridge:(SINCO|SMC-PAD Bluetooth).*"
)

# instance numbering scheme: pad N's slot S lives at mod-host instance
# 1000 + N*10 + S - arbitrary but fixed, with headroom for 16 pads x 3 slots.
_SLOT_INSTANCE_BASE = 1000
MASTER_INSTANCE = 1
MASTER_CLIENT = f"effect_{MASTER_INSTANCE}"
MASTER_LIMITER_INSTANCE = 2
MASTER_LIMITER_CLIENT = f"effect_{MASTER_LIMITER_INSTANCE}"

# (pad_number, slot_index) -> plugin_id currently live in mod-host for that
# slot, so apply_pad_effects only remove+add when the plugin actually
# changed (a param-only change is much cheaper, see set_effect_param).
_live_slot_plugin: dict[tuple[int, int], str | None] = {}
_pad_apply_locks: dict[int, asyncio.Lock] = {}
_apply_semaphore = asyncio.Semaphore(4)
_graph_lock = asyncio.Lock()
_supervisor_task: asyncio.Task | None = None
_last_engine_error = ""
_master_available = False
_master_volume = 100.0
_master_muted = False
_master_limiter_available = False
_master_limiter_enabled = True
_master_limiter_threshold_db = -1.0


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
    async with _graph_lock:
        await asyncio.to_thread(
            jackgraph.connect_pattern_to_all, HARDWARE_MIDI_PATTERN, rf".*:{re.escape(midi.INPUT_PORT_NAME)}$"
        )
    sfizz_proc.set_recovery_handler(_recover_sfizz)
    sfizz_proc.start_watchdog()
    global _supervisor_task
    if _supervisor_task is None:
        _supervisor_task = asyncio.create_task(_supervise_engine())


async def _recover_sfizz(client: str) -> bool:
    global _last_engine_error
    try:
        settings = await asyncio.to_thread(storage.get_settings)
        if client == METRONOME_CLIENT:
            return await apply_metronome_style(settings.get("metronome_style", metronome_sounds.DEFAULT_STYLE))
        if client == PREVIEW_CLIENT:
            if _last_preview_kit is None:
                return False
            return await apply_preview_kit(_last_preview_kit)
        match = re.fullmatch(r"diakopad_pad(\d{2})", client)
        if match is None:
            return False
        pads = await asyncio.to_thread(storage.list_pads)
        effects = await asyncio.to_thread(storage.list_pad_effects)
        return await apply_pad(int(match.group(1)), pads, settings, effects)
    except Exception as exc:  # pragma: no cover - defensive recovery path
        _last_engine_error = f"recuperação do {client}: {exc}"
        logger.exception(_last_engine_error)
        return False


def _refresh_hardware_connections() -> None:
    jackgraph.connect_pattern_to_all(HARDWARE_MIDI_PATTERN, rf".*:{re.escape(midi.INPUT_PORT_NAME)}$")
    jackgraph.connect_pattern_to_all(
        rf"{re.escape(midi.HARDWARE_OUTPUT_PORT_NAME)}$", r"^diakopad_pad\d\d:input$"
    )
    jackgraph.connect_pattern_to_all(rf"{re.escape(midi.OUTPUT_PORT_NAME)}$", r"^diakopad_pad\d\d:input$")
    jackgraph.connect_pattern_to_all(
        rf"{re.escape(midi.METRONOME_OUTPUT_PORT_NAME)}$", rf"^{re.escape(METRONOME_CLIENT)}:input$"
    )


def _schedule_players_without_ports() -> None:
    # PREVIEW_CLIENT is deliberately NOT included here: unlike the 16 pads
    # and the metronome, it only exists transiently while kit-browse mode is
    # active (see apply_preview_kit/stop_preview), so keeping an 18th sfizz
    # process alive at idle just to watch its ports would be pure waste.
    clients = [sfizz_proc.client_name(n) for n in range(1, 17)] + [METRONOME_CLIENT]
    for client in clients:
        if sfizz_proc.is_running(client) and not jackgraph.has_port(f"{client}:output_1"):
            logger.warning("sfizz for %s is alive without JACK ports; scheduling recovery", client)
            sfizz_proc.schedule_recovery(client)


async def _supervise_engine(interval: float = 3.0) -> None:
    global _last_engine_error, _live_slot_plugin, _master_available, _master_limiter_available
    jack_was_available = False
    while True:
        await asyncio.sleep(interval)
        jack_is_available = await asyncio.to_thread(jackgraph.available)
        if jack_is_available:
            async with _graph_lock:
                await asyncio.to_thread(_refresh_hardware_connections)
                await asyncio.to_thread(_schedule_players_without_ports)
        settings = await asyncio.to_thread(storage.get_settings)
        effects = await asyncio.to_thread(storage.list_pad_effects)
        if modhost_client.is_configured() and not modhost_client.is_alive():
            if not await asyncio.to_thread(modhost_client.restart):
                _last_engine_error = "mod-host não reiniciou; rotas diretas restauradas"
                _live_slot_plugin.clear()
                _master_available = False
                _master_limiter_available = False
                await rewire_audio_routes(effects)
            else:
                _live_slot_plugin.clear()
                _master_available = False
                _master_limiter_available = False
                await apply_master(
                    float(settings.get("master_volume", 100)), settings.get("master_muted") == "1",
                    settings.get("master_limiter_enabled", "1") == "1",
                    float(settings.get("master_limiter_threshold_db", -1)),
                )
                await rewire_audio_routes(effects)
        elif jack_is_available and not jack_was_available:
            await apply_master(
                float(settings.get("master_volume", 100)), settings.get("master_muted") == "1",
                settings.get("master_limiter_enabled", "1") == "1",
                float(settings.get("master_limiter_threshold_db", -1)),
            )
            await rewire_audio_routes(effects)
        jack_was_available = jack_is_available


def engine_status() -> dict:
    return {
        "jack": jackgraph.available(),
        "modhost": modhost_client.is_alive(),
        "pads": {str(n): sfizz_proc.is_running(sfizz_proc.client_name(n)) for n in range(1, 17)},
        "metronome": sfizz_proc.is_running(METRONOME_CLIENT),
        "master": master_state(),
        "cpu_percent": system_metrics.cpu_percent(),
        "last_error": _last_engine_error or None,
    }


def master_state() -> dict:
    return {
        "available": _master_available,
        "volume": _master_volume,
        "muted": _master_muted,
        "limiter_available": _master_limiter_available,
        "limiter_enabled": _master_limiter_enabled,
        "limiter_threshold_db": _master_limiter_threshold_db,
    }


def _master_destinations() -> tuple[str, str]:
    if _master_available:
        if _master_limiter_available:
            config = effects_catalog.master_limiter_config()
            return f"{MASTER_LIMITER_CLIENT}:{config['in_ports'][0]}", f"{MASTER_LIMITER_CLIENT}:{config['in_ports'][1]}"
        config = effects_catalog.master_gain_config()
        return f"{MASTER_CLIENT}:{config['in_ports'][0]}", f"{MASTER_CLIENT}:{config['in_ports'][1]}"
    return MASTER_L, MASTER_R


def _wire_master_output(config: dict, limiter_config: dict | None = None) -> None:
    jackgraph.disconnect_all(rf"^{re.escape(MASTER_CLIENT)}:output_.$")
    jackgraph.connect(f"{MASTER_CLIENT}:{config['out_ports'][0]}", MASTER_L)
    jackgraph.connect(f"{MASTER_CLIENT}:{config['out_ports'][1]}", MASTER_R)
    if limiter_config is not None:
        jackgraph.disconnect_all(rf"^{re.escape(MASTER_LIMITER_CLIENT)}:output_.$")
        jackgraph.connect(f"{MASTER_LIMITER_CLIENT}:{limiter_config['out_ports'][0]}", f"{MASTER_CLIENT}:{config['in_ports'][0]}")
        jackgraph.connect(f"{MASTER_LIMITER_CLIENT}:{limiter_config['out_ports'][1]}", f"{MASTER_CLIENT}:{config['in_ports'][1]}")


async def apply_master(
    volume: float, muted: bool, limiter_enabled: bool = True, limiter_threshold_db: float = -1.0
) -> bool:
    global _master_available, _master_volume, _master_muted, _master_limiter_available, _master_limiter_enabled, _master_limiter_threshold_db
    _master_volume = max(0.0, min(100.0, volume))
    _master_muted = muted
    _master_limiter_enabled = limiter_enabled
    _master_limiter_threshold_db = max(-12.0, min(0.0, limiter_threshold_db))
    config = effects_catalog.master_gain_config()
    limiter_config = effects_catalog.master_limiter_config()
    had_master = _master_available
    if not config.get("lv2_uri"):
        _master_available = False
        _master_limiter_available = False
        if had_master:
            await rewire_audio_routes(await asyncio.to_thread(storage.list_pad_effects))
        return False
    needs_route_rewire = False
    async with _graph_lock:
        if not _master_available:
            if not await modhost_client.add(config["lv2_uri"], MASTER_INSTANCE):
                _master_available = False
                _master_limiter_available = False
                return False
            _master_available = True
        if limiter_config.get("lv2_uri") and not _master_limiter_available:
            _master_limiter_available = await modhost_client.add(limiter_config["lv2_uri"], MASTER_LIMITER_INSTANCE)
            needs_route_rewire = _master_limiter_available
        await asyncio.to_thread(_wire_master_output, config, limiter_config if _master_limiter_available else None)
        if _master_limiter_available:
            threshold = 10 ** (_master_limiter_threshold_db / 20.0)
            limiter_applied = await modhost_client.param_set(
                MASTER_LIMITER_INSTANCE, limiter_config["symbol"], threshold
            )
            limiter_applied = await modhost_client.param_set(
                MASTER_LIMITER_INSTANCE, limiter_config["enabled_symbol"], 1.0 if limiter_enabled else 0.0
            ) and limiter_applied
            if not limiter_applied:
                _master_limiter_available = False
                await asyncio.to_thread(_wire_master_output, config)
                needs_route_rewire = True
        gain = config["min"]
        if not muted:
            gain += (config["max"] - config["min"]) * (_master_volume / 100.0)
        applied = await modhost_client.param_set(MASTER_INSTANCE, config["symbol"], gain)
        if not applied:
            _master_available = False
            needs_route_rewire = True
    if needs_route_rewire:
        await rewire_audio_routes(await asyncio.to_thread(storage.list_pad_effects))
    return applied


async def panic(pads: list[dict]) -> bool:
    """Stops every transport, silences active MIDI notes and mutes the master.

    A one-shot sfizz region ignores note-off, so the master mute is the final
    safety net that guarantees silence when a gain plugin is available.
    """
    await sequencer.stop()
    await metronome.stop()
    looper.stop()
    await asyncio.to_thread(midi.all_notes_off, midi.MIDI_CHANNEL)
    for pad in pads:
        await asyncio.to_thread(midi.note_off, midi.MIDI_CHANNEL, pad["midi_note"])
    await asyncio.to_thread(sfizz_proc.emergency_stop_all)
    applied = await apply_master(_master_volume, True, _master_limiter_enabled, _master_limiter_threshold_db)
    return applied


async def restart_engine(pads: list[dict], settings: dict, pad_effects: list[dict]) -> dict:
    """Rebuilds the controllable engine graph without restarting FastAPI."""
    global _live_slot_plugin, _master_available, _master_limiter_available
    if modhost_client.is_configured():
        await asyncio.to_thread(modhost_client.restart)
    _live_slot_plugin.clear()
    _master_available = False
    _master_limiter_available = False
    await apply_master(
        float(settings.get("master_volume", 100)), settings.get("master_muted") == "1",
        settings.get("master_limiter_enabled", "1") == "1",
        float(settings.get("master_limiter_threshold_db", -1)),
    )
    await apply_all_pads(pads, settings, pad_effects)
    await apply_metronome_style(settings.get("metronome_style", metronome_sounds.DEFAULT_STYLE))
    return engine_status()


async def apply_pad(pad_number: int, pads: list[dict], settings: dict, pad_effects: list[dict]) -> bool:
    """Rerenders one pad's .sfz, (re)spawns its sfizz instance, and rewires
    its MIDI inputs + effect chain. Called after a sample/note/volume/pan/
    tone change (anything that needs the sfizz process itself restarted).
    Returns True if the real engine actually picked it up (false on dev
    machines without sfizz)."""
    lock = _pad_apply_locks.setdefault(pad_number, asyncio.Lock())
    async with lock:
        pad = next((p for p in pads if p["pad_number"] == pad_number), None)
        if pad is None:
            return False
        client = sfizz_proc.client_name(pad_number)
        if not pad.get("filename"):
            await asyncio.to_thread(sfizz_proc.stop, client)
            return False

        sfz_path = await asyncio.to_thread(sfz.write_pad_kit, pad_number, pad, settings)
        if not await asyncio.to_thread(sfizz_proc.spawn, client, sfz_path):
            return False

        async with _graph_lock:
            if not await jackgraph.wait_for_port_async(f"{client}:output_1"):
                logger.warning("sfizz JACK ports for pad %d never appeared", pad_number)
                sfizz_proc.schedule_recovery(client)
                return False

            await asyncio.to_thread(
                jackgraph.connect_pattern_to_all,
                rf"{re.escape(midi.HARDWARE_OUTPUT_PORT_NAME)}$",
                f"^{re.escape(client)}:input$",
            )
            await asyncio.to_thread(
                jackgraph.connect_pattern_to_all,
                rf"{re.escape(midi.OUTPUT_PORT_NAME)}$",
                f"^{re.escape(client)}:input$",
            )
            await _apply_pad_effects_unlocked(pad_number, pad_effects)
            sfizz_proc.mark_recovered(client)
        return True


async def apply_all_pads(pads: list[dict], settings: dict, pad_effects: list[dict]) -> None:
    """Used after a global settings change (sustain/velocity, affects every
    pad's rendered .sfz) and after an engine reset. Must call apply_pad() for
    every pad, not just the ones that currently have a filename - apply_pad()
    is what actually stops a pad's sfizz instance when it has none, and
    skipping that call for an empty pad here used to leave a just-cleared
    pad's old instance running (and still wired to hardware MIDI), silently
    playing the previous sample forever. apply_pad() no-ops cheaply for a pad
    that's already stopped, so calling it for all 16 costs nothing extra.
    Scene switching uses apply_changed_pads() instead, since unlike a
    settings change it usually only touches a handful of pads."""
    async def apply_one(pad: dict) -> None:
        async with _apply_semaphore:
            await apply_pad(pad["pad_number"], pads, settings, pad_effects)

    await asyncio.gather(*(apply_one(pad) for pad in pads))


# Pad fields that feed into render_pad_sfz() (see sfz.py) - anything else
# (display_name, pad_number itself) can't change what a pad sounds like.
_SFZ_RELEVANT_FIELDS = ("sample_id", "midi_note", "volume_db", "pan", "cutoff_hz")


async def apply_changed_pads(
    old_pads: list[dict], new_pads: list[dict], settings: dict, pad_effects: list[dict]
) -> None:
    """Like apply_all_pads(), but only respawns a pad's sfizz instance (kill +
    spawn a new OS process + wait for its JACK ports + rewire MIDI/effects -
    the expensive part of a scene switch) when one of _SFZ_RELEVANT_FIELDS
    actually differs from before. A pad whose sound is unchanged keeps its
    already-running instance and only gets its effect chain re-applied
    (apply_pad_effects() is a cheap, already-diffed mod-host-only call), so a
    scene switch that shares most pads with the previous one only pays for
    the pads that actually changed. Only valid when `settings` itself hasn't
    changed between old_pads and new_pads (true for scene switching - scenes
    don't carry sustain_mode/velocity_sensitive), since those affect every
    pad's rendered .sfz regardless of its own fields."""
    old_by_number = {p["pad_number"]: p for p in old_pads}

    async def apply_one(pad: dict) -> None:
        old = old_by_number.get(pad["pad_number"])
        changed = old is None or any(old.get(f) != pad.get(f) for f in _SFZ_RELEVANT_FIELDS)
        if changed:
            async with _apply_semaphore:
                await apply_pad(pad["pad_number"], new_pads, settings, pad_effects)
        else:
            await apply_pad_effects(pad["pad_number"], pad_effects)

    await asyncio.gather(*(apply_one(pad) for pad in new_pads))


async def apply_pad_effects(pad_number: int, pad_effects: list[dict]) -> None:
    """(Re)creates/removes mod-host instances for this pad's slots as needed
    and rebuilds its serial chain. Called both from apply_pad() (after a
    respawn, since the pad's JACK ports are new) and directly when only a
    slot's plugin assignment changes (no sfizz restart needed)."""
    async with _graph_lock:
        await _apply_pad_effects_unlocked(pad_number, pad_effects)


async def _apply_pad_effects_unlocked(pad_number: int, pad_effects: list[dict]) -> None:
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
            if plugin_id is not None and modhost_client.is_alive():
                plugin = effects_catalog.PLUGIN_CATALOG.get(plugin_id)
                if plugin and plugin["lv2_uri"]:
                    loaded = await modhost_client.add(plugin["lv2_uri"], instance)
                else:
                    logger.warning("no LV2 URI configured for effect %r; leaving pad %d slot %d empty", plugin_id, pad_number, row["slot_index"])
            _live_slot_plugin[key] = plugin_id if loaded else None
        if _live_slot_plugin.get(key):
            # effective_params(): defaults + stored values, dropping symbols
            # that no longer belong to the current plugin (scenes saved before
            # a catalog swap, e.g. mda/Ambience -> Dragonfly reverb) and
            # clamping to each param's LV2 range.
            for symbol, value in effects_catalog.effective_params(plugin_id, row["params"]).items():
                await modhost_client.param_set(instance, symbol, value)

    _rewire_pad_chain(pad_number, slots)


async def rewire_audio_routes(pad_effects: list[dict]) -> None:
    """Restores every audio route after JACK or mod-host recovers/fails."""
    for pad_number in range(1, 17):
        await apply_pad_effects(pad_number, pad_effects)
    await _rewire_metronome()


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
        for index, in_port in enumerate(in_ports):
            jackgraph.connect(prev_out[min(index, len(prev_out) - 1)], f"{slot_client}:{in_port}")
        if len(out_ports) == 1:
            prev_out = (f"{slot_client}:{out_ports[0]}",) * 2
        else:
            prev_out = (f"{slot_client}:{out_ports[0]}", f"{slot_client}:{out_ports[1]}")

    master_l, master_r = _master_destinations()
    jackgraph.connect(prev_out[0], master_l)
    jackgraph.connect(prev_out[1], master_r)


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
    async with _graph_lock:
        return await asyncio.to_thread(_apply_metronome_style_sync, style)


async def _rewire_metronome() -> bool:
    async with _graph_lock:
        return await asyncio.to_thread(_rewire_metronome_sync)


def _rewire_metronome_sync() -> bool:
    if not jackgraph.wait_for_port(f"{METRONOME_CLIENT}:output_1", timeout=0.1):
        return False
    master_l, master_r = _master_destinations()
    audio_left = jackgraph.connect(f"{METRONOME_CLIENT}:output_1", master_l)
    audio_right = jackgraph.connect(f"{METRONOME_CLIENT}:output_2", master_r)
    midi_connected = jackgraph.connect_pattern_to_all(
        rf"{re.escape(midi.METRONOME_OUTPUT_PORT_NAME)}$", f"^{re.escape(METRONOME_CLIENT)}:input$"
    )
    return audio_left and audio_right and midi_connected


def _apply_metronome_style_sync(style: str) -> bool:
    sfz_path = metronome_sounds.write_metronome_sfz(style)
    if not sfizz_proc.spawn(METRONOME_CLIENT, sfz_path):
        return False
    if not jackgraph.wait_for_port(f"{METRONOME_CLIENT}:output_1"):
        logger.warning("sfizz JACK ports for the metronome never appeared")
        sfizz_proc.schedule_recovery(METRONOME_CLIENT)
        return False
    master_l, master_r = _master_destinations()
    audio_left = jackgraph.connect(f"{METRONOME_CLIENT}:output_1", master_l)
    audio_right = jackgraph.connect(f"{METRONOME_CLIENT}:output_2", master_r)
    midi_connected = jackgraph.connect_pattern_to_all(
        rf"{re.escape(midi.METRONOME_OUTPUT_PORT_NAME)}$", f"^{re.escape(METRONOME_CLIENT)}:input$"
    )
    sfizz_proc.mark_recovered(METRONOME_CLIENT)
    return audio_left and audio_right and midi_connected


PREVIEW_CLIENT = "diakopad_preview"
_last_preview_kit: dict | None = None


async def apply_preview_kit(kit: dict) -> bool:
    """(Re)spawns the dedicated kit-browse preview sfizz instance with every
    populated pad-role of `kit` as its own fixed-note region (see
    sfz.write_preview_kit) and wires it into the graph - same pattern as the
    metronome, but respawned once per navigation step (not once per preview
    hit) so auditioning individual sounds stays cheap. Never touches the 16
    live pads or DiakoPad-in."""
    global _last_preview_kit
    _last_preview_kit = kit
    async with _graph_lock:
        return await asyncio.to_thread(_apply_preview_kit_sync, kit)


def _apply_preview_kit_sync(kit: dict) -> bool:
    sfz_path = sfz.write_preview_kit(kit)
    if not sfizz_proc.spawn(PREVIEW_CLIENT, sfz_path):
        return False
    if not jackgraph.wait_for_port(f"{PREVIEW_CLIENT}:output_1"):
        logger.warning("sfizz JACK ports for the kit preview never appeared")
        sfizz_proc.schedule_recovery(PREVIEW_CLIENT)
        return False
    master_l, master_r = _master_destinations()
    audio_left = jackgraph.connect(f"{PREVIEW_CLIENT}:output_1", master_l)
    audio_right = jackgraph.connect(f"{PREVIEW_CLIENT}:output_2", master_r)
    midi_connected = jackgraph.connect_pattern_to_all(
        rf"{re.escape(midi.PREVIEW_OUTPUT_PORT_NAME)}$", f"^{re.escape(PREVIEW_CLIENT)}:input$"
    )
    sfizz_proc.mark_recovered(PREVIEW_CLIENT)
    return audio_left and audio_right and midi_connected


async def stop_preview() -> None:
    """Stops the kit-browse preview instance - called unconditionally on both
    Confirm and Back, so it never lingers once browsing ends."""
    global _last_preview_kit
    _last_preview_kit = None
    await asyncio.to_thread(sfizz_proc.stop, PREVIEW_CLIENT)


async def shutdown() -> None:
    global _supervisor_task
    if _supervisor_task is not None:
        _supervisor_task.cancel()
        _supervisor_task = None
    await sfizz_proc.stop_watchdog()
    await asyncio.to_thread(sfizz_proc.stop_all)
    await asyncio.to_thread(modhost_client.stop)

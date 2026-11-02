"""Central audio-engine orchestrator.

Owns the per-pad sfizz instances, the shared mod-host process (per-pad
effect-slot chains, see effects_catalog.py), the sequencer/looper trigger
port, and wires everything together over JACK. DiakoPad owns every audio
process directly - a pad reload is just "respawn this one instance", and an
effect param change is just a mod-host param_set with no restart at all.

JACK topology per pad:
  SMC-PAD hardware -> DiakoPad-in -> DiakoPad-hardware-out -> diakopad_padNN_<gen>:input
  DiakoPad-trigger-out (sequencer/looper, see engine/trigger.py) --> same inputs
  diakopad_padNN_<gen>:output_1/2 --> slot1 (if any) --> slot2 (if any) -->
                                 slot3 (if any) --> master
                                                +--> shared FX send (engine/fx_buses.py)
  (skips straight to the master when every slot is empty)

Each pad's sfizz loads a bank with every sound the pad has live and in every
saved scene (see sfz.py); a scene switch selects them with one CC per pad
(fast path). Anything not in the bank yet goes through a prepared swap: the
next sfizz generation starts while the current one keeps playing, then they
trade places and the old one rings out (see sfizz_proc's prepare/commit).

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
import sample_trim
import sfz
import storage
import system_metrics
from engine import effects_catalog, fx_buses, jackgraph, looper, metronome, metronome_sounds, modhost_client, pwclock, sequencer, sfizz_proc, tempo

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
# Shared FX (engine/fx_buses.py): created once, never removed.
BUS_INSTANCES = {"hall": 3, "plate": 4, "delay": 5}
MATRIX_INSTANCES = (8, 9)  # pads 1-8, pads 9-16
_buses_ready = False
_live_sends: dict[tuple[int, str], float] = {}
_main_loop: asyncio.AbstractEventLoop | None = None
# pad_number -> what its current sfizz generation has loaded:
# {"name", "note", "sig", "keys": [variant key...], "slot": selected index}
_banks: dict[int, dict] = {}

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
    # Pin the PipeWire clock BEFORE spawning anything: LV2 plugins and sfizz
    # instantiate at the graph rate and can't follow a later rate change.
    if (await asyncio.to_thread(pwclock.enforce))["changed"]:
        await asyncio.sleep(1.0)  # let the graph settle on the new rate/quantum
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    tempo.add_listener(_on_tempo_change)
    # Private banks of generations from a previous run (killed with the
    # service, never reaped) - nothing references them any more.
    for stale in sfz.live_bank_dir().glob("pad*_*.sfz"):
        stale.unlink(missing_ok=True)
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


def _connect_pad_midi(name: str) -> None:
    for src in (midi.HARDWARE_OUTPUT_PORT_NAME, midi.OUTPUT_PORT_NAME):
        jackgraph.connect_pattern_to_all(rf"{re.escape(src)}$", f"^{re.escape(name)}:input$")


def _refresh_hardware_connections() -> None:
    jackgraph.connect_pattern_to_all(HARDWARE_MIDI_PATTERN, rf".*:{re.escape(midi.INPUT_PORT_NAME)}$")
    # Only the CURRENT generation of each pad gets MIDI - a retired one
    # ringing out must never be re-armed by this periodic refresh.
    for n in range(1, 17):
        client = sfizz_proc.client_name(n)
        if sfizz_proc.is_running(client):
            _connect_pad_midi(sfizz_proc.jack_name(client))
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
        if sfizz_proc.is_running(client) and not jackgraph.has_port(f"{sfizz_proc.jack_name(client)}:output_1"):
            logger.warning("sfizz for %s is alive without JACK ports; scheduling recovery", client)
            sfizz_proc.schedule_recovery(client)


async def _supervise_engine(interval: float = 3.0) -> None:
    global _last_engine_error, _live_slot_plugin, _master_available, _master_limiter_available
    jack_was_available = False
    while True:
        await asyncio.sleep(interval)
        clock = await asyncio.to_thread(pwclock.enforce)
        if pwclock.gave_up and not _last_engine_error:
            _last_engine_error = "outro programa insiste em mudar a taxa de amostragem do PipeWire (widget do Ubuntu Studio?)"
        if clock["rate_changed"]:
            # Something (e.g. Ubuntu Studio's audio widget) moved the graph to
            # another sample rate under the running engine: the clock is back,
            # but plugins/sfizz were instantiated at the other rate - rebuild.
            await asyncio.sleep(1.0)
            _last_engine_error = "clock do PipeWire foi alterado por outro programa; motor reiniciado"
            await restart_engine(
                await asyncio.to_thread(storage.list_pads),
                await asyncio.to_thread(storage.get_settings),
                await asyncio.to_thread(storage.list_pad_effects),
            )
            continue
        jack_is_available = await asyncio.to_thread(jackgraph.available)
        if jack_is_available:
            async with _graph_lock:
                await asyncio.to_thread(_refresh_hardware_connections)
                await asyncio.to_thread(_schedule_players_without_ports)
        settings = await asyncio.to_thread(storage.get_settings)
        effects = await asyncio.to_thread(storage.list_pad_effects)
        if modhost_client.is_configured() and not modhost_client.is_alive():
            _reset_buses()
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
                await apply_fx_buses(
                    await asyncio.to_thread(storage.list_fx_buses),
                    await asyncio.to_thread(storage.list_pad_sends),
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


def report_error(message: str) -> None:
    """Registra um erro vindo de fora do orquestrador (ex.: falha no startup)
    para aparecer em engine_status()["last_error"] na UI."""
    global _last_engine_error
    _last_engine_error = message


def engine_status() -> dict:
    return {
        "jack": jackgraph.available(),
        "modhost": modhost_client.is_alive(),
        "pads": {str(n): sfizz_proc.is_running(sfizz_proc.client_name(n)) for n in range(1, 17)},
        "metronome": sfizz_proc.is_running(METRONOME_CLIENT),
        "fx_buses": _buses_ready,
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
    _reset_buses()
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
    await apply_fx_buses(
        await asyncio.to_thread(storage.list_fx_buses), await asyncio.to_thread(storage.list_pad_sends)
    )
    await apply_all_pads(pads, settings, pad_effects)
    await apply_metronome_style(settings.get("metronome_style", metronome_sounds.DEFAULT_STYLE))
    return engine_status()


def _settings_sig(settings: dict) -> tuple:
    return (
        settings.get("sustain_mode", "1"),
        settings.get("velocity_sensitive", "0"),
        settings.get("auto_trim", "1"),
    )


def _attach_trims(rows: list[dict]) -> None:
    """Adds trim_start/trim_end (sample_trim.py, cached in the DB) to rows
    that have a sample. Runs in a worker thread: a first-time analysis
    decodes the file."""
    trims = sample_trim.trims_for(rows, sfz.SAMPLES_DIR)
    for row in rows:
        start, end = trims.get(row.get("sample_id"), (0, None))
        row["trim_start"], row["trim_end"] = start, end


def _variant(source: dict) -> dict:
    filename = source.get("filename")
    return {
        "sample_id": source.get("sample_id") if filename else None,
        "filename": filename,
        "volume_db": source.get("volume_db", 6),
        "pan": source.get("pan", 0),
        "cutoff_hz": source.get("cutoff_hz"),
    }


def _variant_key(variant: dict) -> tuple:
    """What a variant sounds like. Every empty variant is the same silence."""
    if not variant.get("filename"):
        return (None,)
    cutoff = variant.get("cutoff_hz")
    return (
        variant.get("sample_id"),
        round(float(variant.get("volume_db") or 0), 3),
        round(float(variant.get("pan") or 0), 3),
        None if cutoff is None else round(float(cutoff), 1),
    )


def _bank_variants(live: dict, scene_variants: list[dict]) -> list[dict]:
    """Live variant first (index 0, selected when sfizz starts), then every
    other distinct sound the pad has in a saved scene."""
    variants, seen = [], set()
    for v in [_variant(live)] + [_variant(v) for v in scene_variants]:
        key = _variant_key(v)
        if key in seen:
            continue
        seen.add(key)
        variants.append(v)
    return variants[: sfz.MAX_VARIANTS]


def _select_bank_slot(pad_number: int, pad: dict, settings: dict) -> bool:
    """Fast path: if the pad's running bank already has this sound, select it
    with the pad's CC and return True (no process change at all)."""
    bank = _banks.get(pad_number)
    client = sfizz_proc.client_name(pad_number)
    if (
        bank is None
        or not sfizz_proc.is_running(client)
        or bank["name"] != sfizz_proc.jack_name(client)
        or bank["note"] != pad.get("midi_note")
        or bank["sig"] != _settings_sig(settings)
    ):
        return False
    key = _variant_key(_variant(pad))
    if key not in bank["keys"]:
        return False
    slot = bank["keys"].index(key)
    if slot != bank["slot"]:
        if not midi.control_change(midi.MIDI_CHANNEL, sfz.scene_cc(pad_number), slot):
            return False
        bank["slot"] = slot
    return True


async def apply_pad(
    pad_number: int, pads: list[dict], settings: dict, pad_effects: list[dict], force: bool = False
) -> bool:
    """Makes a pad sound like its live DB row. Fast path: the running bank
    already has that sound -> one CC. Otherwise (or with force=True, after a
    settings change / engine reset) a new sfizz generation with a fresh bank
    is prepared while the current one keeps playing, then swapped in.
    Returns True if the real engine actually picked it up (False on dev
    machines without sfizz, or for a pad that is empty everywhere)."""
    lock = _pad_apply_locks.setdefault(pad_number, asyncio.Lock())
    async with lock:
        pad = next((p for p in pads if p["pad_number"] == pad_number), None)
        if pad is None:
            return False
        client = sfizz_proc.client_name(pad_number)

        if not force and _select_bank_slot(pad_number, pad, settings):
            await apply_pad_effects(pad_number, pad_effects)
            return True

        scene_variants = (await asyncio.to_thread(storage.scene_pad_variants)).get(pad_number, [])
        variants = _bank_variants(pad, scene_variants)
        if all(not v["filename"] for v in variants):
            # Empty live and in every scene: nothing to load. The current
            # generation (if any) stops getting notes and rings out.
            async with _graph_lock:
                if sfizz_proc.is_running(client):
                    await asyncio.to_thread(jackgraph.disconnect_all, rf"^{re.escape(sfizz_proc.jack_name(client))}:input$")
                sfizz_proc.retire(client)
            _banks.pop(pad_number, None)
            return False

        await asyncio.to_thread(_attach_trims, variants)
        live = {**pad, "trim_start": variants[0].get("trim_start", 0), "trim_end": variants[0].get("trim_end")}
        tag = sfizz_proc.next_tag(client)
        sfz_path = await asyncio.to_thread(sfz.write_pad_bank, pad_number, live, variants, settings, tag)
        new_name = sfizz_proc.prepare(client, sfz_path)
        if new_name is None:
            return False

        # Waiting for the new ports (most of the latency) happens outside
        # _graph_lock, so pads swapping together prepare in parallel - and
        # the current generation is still playing the whole time.
        if not await jackgraph.wait_for_port_async(f"{new_name}:output_1"):
            logger.warning("sfizz JACK ports for pad %d never appeared", pad_number)
            sfizz_proc.abort(client)
            sfizz_proc.schedule_recovery(client)
            return False

        async with _graph_lock:
            old_name = sfizz_proc.jack_name(client) if sfizz_proc.is_running(client) else None
            await asyncio.to_thread(_connect_pad_midi, new_name)
            if old_name:
                # From here on new hits only reach the new generation; the
                # old one keeps sounding what it already started.
                await asyncio.to_thread(jackgraph.disconnect_all, rf"^{re.escape(old_name)}:input$")
            sfizz_proc.commit(client)
            _banks[pad_number] = {
                "name": new_name,
                "note": pad.get("midi_note"),
                "sig": _settings_sig(settings),
                "keys": [_variant_key(v) for v in variants],
                "slot": 0,
            }
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
            await apply_pad(pad["pad_number"], pads, settings, pad_effects, force=True)

    await asyncio.gather(*(apply_one(pad) for pad in pads))


# Pad fields that feed into render_pad_sfz() (see sfz.py) - anything else
# (display_name, pad_number itself) can't change what a pad sounds like.
_SFZ_RELEVANT_FIELDS = ("sample_id", "midi_note", "volume_db", "pan", "cutoff_hz")


async def apply_changed_pads(
    old_pads: list[dict], new_pads: list[dict], settings: dict, pad_effects: list[dict],
    fx: tuple[dict, list[dict]] | None = None,
) -> None:
    """Scene switch. Every pad whose new sound is already in its bank flips
    with one CC (all of them at once, before any effect work); only pads
    whose sound isn't loaded yet go through a prepared swap - and even those
    keep playing the old sound until the new generation is ready.
    `old_pads` is kept for API compatibility; the banks know what's loaded.
    `fx` = (bus params, sends) of the new state, applied right after the CCs
    so sends/reverbs change together with the sounds."""
    slow = []
    for pad in new_pads:
        if not _select_bank_slot(pad["pad_number"], pad, settings):
            slow.append(pad)
    if fx is not None:
        await apply_fx_buses(*fx)
    await asyncio.gather(*(apply_pad_effects(p["pad_number"], pad_effects) for p in new_pads if p not in slow))

    async def swap(pad: dict) -> None:
        async with _apply_semaphore:
            await apply_pad(pad["pad_number"], new_pads, settings, pad_effects)

    await asyncio.gather(*(swap(pad) for pad in slow))


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
    if _buses_ready:
        async with _graph_lock:
            await asyncio.to_thread(_wire_buses)
    for pad_number in range(1, 17):
        await apply_pad_effects(pad_number, pad_effects)
    await _rewire_metronome()


def _rewire_pad_chain(pad_number: int, slots: list[dict]) -> None:
    """Makes the pad's audio chain match its slots - by diff: only the edges
    that actually change are touched. Every JACK connect/disconnect makes
    PipeWire reconfigure the graph, and the old disconnect-everything-and-
    reconnect version cost ~1 xrun per scene switch even when nothing
    changed (measured on the laptop, nov/2026)."""
    client = sfizz_proc.client_name(pad_number)
    if not jackgraph.available():
        return

    # The current generation is wired even if the process isn't confirmed
    # running yet (connect() to a missing port is a harmless no-op). Retired
    # generations still ringing out are fed through the same chain.
    heads = [sfizz_proc.jack_name(client)] + sfizz_proc.retired_names(client)
    outs = [(f"{h}:output_1", f"{h}:output_2") for h in heads]
    owned = {port for pair in outs for port in pair}
    desired: set[tuple[str, str]] = set()
    for row in slots:
        plugin_id = _live_slot_plugin.get((pad_number, row["slot_index"]))
        if not plugin_id:
            continue
        plugin = effects_catalog.PLUGIN_CATALOG[plugin_id]
        slot_client = _effect_client(_slot_instance(pad_number, row["slot_index"]))
        in_ports, out_ports = plugin["in_ports"], plugin["out_ports"]
        for prev_out in outs:
            for index, in_port in enumerate(in_ports):
                desired.add((prev_out[min(index, len(prev_out) - 1)], f"{slot_client}:{in_port}"))
        if len(out_ports) == 1:
            outs = [(f"{slot_client}:{out_ports[0]}",) * 2]
        else:
            outs = [(f"{slot_client}:{out_ports[0]}", f"{slot_client}:{out_ports[1]}")]
        owned |= {port for pair in outs for port in pair}

    master_l, master_r = _master_destinations()
    send_port = _matrix_input_port(pad_number) if _buses_ready else None
    for prev_out in outs:
        desired |= {(prev_out[0], master_l), (prev_out[1], master_r)}
        if send_port:
            # L and R both into one matrix input: JACK sums them (mono send).
            desired |= {(prev_out[0], send_port), (prev_out[1], send_port)}
    jackgraph.sync_edges(owned, desired)


# --- Shared FX buses (engine/fx_buses.py) ---------------------------------


def _matrix_input_port(pad_number: int) -> str:
    index, inp = fx_buses.matrix_slot(pad_number)
    return f"{_effect_client(MATRIX_INSTANCES[index])}:in{inp}"


def _reset_buses() -> None:
    """mod-host is going away (restart/crash): the bus instances go with it."""
    global _buses_ready
    _buses_ready = False
    _live_sends.clear()


def _wire_buses() -> None:
    master_l, master_r = _master_destinations()
    for bus, instance in BUS_INSTANCES.items():
        entry = fx_buses.BUS_CATALOG[bus]
        client = _effect_client(instance)
        jackgraph.disconnect_all(rf"^{re.escape(client)}:({'|'.join(map(re.escape, entry['out_ports']))})$")
        jackgraph.connect(f"{client}:{entry['out_ports'][0]}", master_l)
        jackgraph.connect(f"{client}:{entry['out_ports'][1]}", master_r)
        for matrix in MATRIX_INSTANCES:
            src = f"{_effect_client(matrix)}:out{fx_buses.BUS_MATRIX_OUTPUT[bus]}"
            for in_port in entry["in_ports"]:
                jackgraph.connect(src, f"{client}:{in_port}")


async def _create_buses() -> bool:
    """Adds the 2 matrices + 3 bus plugins once. All-or-nothing: on any
    failure the partial set is removed so the next attempt starts clean."""
    wanted = [(fx_buses.MATRIX_URI, m) for m in MATRIX_INSTANCES] + [
        (fx_buses.BUS_CATALOG[bus]["lv2_uri"], inst) for bus, inst in BUS_INSTANCES.items()
    ]
    added = []
    for uri, instance in wanted:
        if not uri or not await modhost_client.add(uri, instance):
            for done in added:
                await modhost_client.remove(done)
            return False
        added.append(instance)
    for matrix in MATRIX_INSTANCES:
        if not await jackgraph.wait_for_port_async(f"^{_effect_client(matrix)}:out1$"):
            logger.warning("shared FX matrix %d ports never appeared", matrix)
    for bus, instance in BUS_INSTANCES.items():
        port = fx_buses.BUS_CATALOG[bus]["out_ports"][0]
        if not await jackgraph.wait_for_port_async(f"^{_effect_client(instance)}:{re.escape(port)}$"):
            logger.warning("shared FX bus %s ports never appeared", bus)
    # The matrix starts as identity (in_n -> out_n): silence every crosspoint.
    for matrix in MATRIX_INSTANCES:
        for i in range(1, fx_buses.MATRIX_INPUTS + 1):
            for o in range(1, fx_buses.MATRIX_INPUTS + 1):
                await modhost_client.param_set(matrix, f"mix_{i}_{o}", 0.0)
    return True


async def _apply_send(pad_number: int, bus: str, level: float) -> bool:
    index, inp = fx_buses.matrix_slot(pad_number)
    ok = await modhost_client.param_set(
        MATRIX_INSTANCES[index], f"mix_{inp}_{fx_buses.BUS_MATRIX_OUTPUT[bus]}", fx_buses.send_gain(level)
    )
    if ok:
        _live_sends[(pad_number, bus)] = level
    return ok


async def _apply_bus_values(bus: str, params: dict) -> None:
    values = fx_buses.plugin_values(bus, fx_buses.effective_params(bus, params), tempo.get())
    for symbol, value in values.items():
        await modhost_client.param_set(BUS_INSTANCES[bus], symbol, value)


async def apply_fx_buses(bus_params: dict, sends: list[dict]) -> bool:
    """Brings the shared FX in line with the DB: creates them the first time
    (and after a mod-host restart), then only sets numbers - the bus params
    and the sends that changed. Never adds or removes a plugin on a scene
    switch."""
    global _buses_ready
    if not modhost_client.is_configured() or not modhost_client.is_alive():
        return False
    newly_ready = False
    async with _graph_lock:
        if not _buses_ready:
            if not await _create_buses():
                logger.warning("shared FX buses unavailable; pads keep their direct routes")
                return False
            _buses_ready = newly_ready = True
            _live_sends.clear()
            await asyncio.to_thread(_wire_buses)
        for bus in fx_buses.BUSES:
            await _apply_bus_values(bus, bus_params.get(bus) or {})
        for send in sends:
            key = (send["pad_number"], send["bus"])
            if send["bus"] in BUS_INSTANCES and _live_sends.get(key) != send["level"]:
                await _apply_send(send["pad_number"], send["bus"], send["level"])
    if newly_ready:
        # Existing pad chains predate the matrices: add their send connections.
        await rewire_audio_routes(await asyncio.to_thread(storage.list_pad_effects))
    return True


async def set_pad_send(pad_number: int, bus: str, level: float) -> bool:
    if not _buses_ready:
        return False
    return await _apply_send(pad_number, bus, level)


async def set_fx_bus_param(bus: str, params: dict) -> bool:
    """Re-sends a bus's values after one of its params changed."""
    if not _buses_ready:
        return False
    await _apply_bus_values(bus, params)
    return True


async def _apply_delay_tempo(bpm: float) -> None:
    if not _buses_ready:
        return
    params = fx_buses.effective_params("delay", (await asyncio.to_thread(storage.list_fx_buses)).get("delay"))
    await modhost_client.param_set(BUS_INSTANCES["delay"], "dt0", fx_buses.delay_time_ms(bpm, params["beats"]))


def _on_tempo_change(bpm: float) -> None:
    """tempo listener (may fire off the event loop thread, e.g. from MIDI)."""
    loop = _main_loop
    if loop is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_apply_delay_tempo(bpm)))


async def set_effect_param(pad_number: int, slot_index: int, symbol: str, value: float) -> bool:
    """Cheap path for an effect slider change: no process restart, no
    rewiring, just a mod-host param_set."""
    instance = _slot_instance(pad_number, slot_index)
    return await modhost_client.param_set(instance, symbol, value)


METRONOME_CLIENT = "diakopad_metronome"


_metronome_style_live: str | None = None


async def apply_metronome_style(style: str, only_if_changed: bool = False) -> bool:
    """(Re)spawns the dedicated metronome sfizz instance with the chosen
    click style and wires it into the graph - same pattern as a pad, but it
    isn't one of the 16 performance pads and only ever plays the two fixed
    click notes (see engine/metronome_sounds.py). only_if_changed: skip the
    respawn when that style is already running (scene switches)."""
    global _metronome_style_live
    if only_if_changed and style == _metronome_style_live and sfizz_proc.is_running(METRONOME_CLIENT):
        return True
    async with _graph_lock:
        ok = await asyncio.to_thread(_apply_metronome_style_sync, style)
    _metronome_style_live = style if ok else None
    return ok


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
    pads = [dict(p) for p in kit.get("pads", [])]
    if storage.get_settings().get("auto_trim", "1") == "1":
        _attach_trims(pads)
    kit = {**kit, "pads": pads}
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

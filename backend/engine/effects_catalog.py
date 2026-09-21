"""Curated catalog of per-pad effect slot plugins.

Deliberately a small, curated set rather than "browse every installed LV2
plugin" - keeps the UI and the mod-host wiring in orchestrator.py simple.
Each entry's LV2 URI and port names are env-overridable (same convention as
the rest of backend/engine/) since the exact plugin available on a given
install is only knowable on-device via `lv2ls`/`lv2info` - see
deploy/install.sh and deploy/install-ubuntu-studio.sh. The parameter list
(symbols/ranges) is NOT env-overridable since that's a lot of surface area;
hand-edit this file once a real plugin has been picked for a catalog slot on
the target device.

Defaults below target the Ubuntu Studio laptop deploy, validated on-device
(set/2026, MARK42, `lv2ls`/`lv2info`): Dragonfly Hall/Plate, ZamVerb and the
LSP modulation suite are NOT on the Zynthian OS bookworm image - the Pi
deploy pins the reverb slot back to mda/Ambience via
DIAKOPAD_FX_REVERB_LV2_URI (see deploy/diakopad.service), and the
plate/IR/chorus/flanger/phaser entries simply stay unloaded there (the slot
is left empty with a warning in orchestrator.py).
"""
from __future__ import annotations

import os


def _ports(env_prefix: str, default_in: str, default_out: str) -> tuple[tuple[str, str], tuple[str, str]]:
    in_ports = tuple(os.environ.get(f"{env_prefix}_IN_PORTS", default_in).split(","))
    out_ports = tuple(os.environ.get(f"{env_prefix}_OUT_PORTS", default_out).split(","))
    return in_ports, out_ports  # type: ignore[return-value]


def _entry(
    env_prefix: str,
    label: str,
    params: list[dict],
    lv2_uri: str = "",
    in_ports: str = "in_l,in_r",
    out_ports: str = "out_l,out_r",
) -> dict:
    """One catalog slot. `lv2_uri`/ports are the validated defaults for the
    reference install (Ubuntu Studio laptop, see README "Achados da
    validação"); env vars still override each field per deployment."""
    ports_in = tuple(os.environ.get(f"{env_prefix}_IN_PORTS", in_ports).split(","))
    ports_out = tuple(os.environ.get(f"{env_prefix}_OUT_PORTS", out_ports).split(","))
    return {
        "label": label,
        "lv2_uri": os.environ.get(f"{env_prefix}_LV2_URI", lv2_uri) or None,
        "in_ports": ports_in,
        "out_ports": ports_out,
        "params": params,
    }


def master_gain_config() -> dict:
    """Runtime configuration for the single master-gain LV2 instance.

    The URI and control symbol vary across installs, so they are
    deployment settings rather than hard-coded assumptions. A missing URI is
    a supported degraded mode: pad chains stay connected directly to JACK.
    """
    in_ports, out_ports = _ports("DIAKOPAD_FX_MASTER_GAIN", "in_l,in_r", "out_l,out_r")
    return {
        "lv2_uri": os.environ.get("DIAKOPAD_FX_MASTER_GAIN_LV2_URI"),
        "in_ports": in_ports,
        "out_ports": out_ports,
        "symbol": os.environ.get("DIAKOPAD_FX_MASTER_GAIN_SYMBOL", "gain"),
        "min": float(os.environ.get("DIAKOPAD_FX_MASTER_GAIN_MIN", "-60")),
        "max": float(os.environ.get("DIAKOPAD_FX_MASTER_GAIN_MAX", "0")),
    }


def master_limiter_config() -> dict:
    """Validated LSP limiter configuration for the Ubuntu Studio target."""
    in_ports, out_ports = _ports("DIAKOPAD_FX_MASTER_LIMITER", "in_l,in_r", "out_l,out_r")
    return {
        "lv2_uri": os.environ.get(
            "DIAKOPAD_FX_MASTER_LIMITER_LV2_URI", "http://lsp-plug.in/plugins/lv2/limiter_stereo"
        ),
        "in_ports": in_ports,
        "out_ports": out_ports,
        "symbol": os.environ.get("DIAKOPAD_FX_MASTER_LIMITER_SYMBOL", "th"),
        "enabled_symbol": os.environ.get("DIAKOPAD_FX_MASTER_LIMITER_ENABLED_SYMBOL", "enabled"),
    }


# Dragonfly's DPF-generated bundles expose generic audio port symbols
# (confirmed via lv2info) shared by every Dragonfly variant and ZamVerb.
DRAGONFLY_IN = "lv2_audio_in_1,lv2_audio_in_2"
DRAGONFLY_OUT = "lv2_audio_out_1,lv2_audio_out_2"

PLUGIN_CATALOG: dict[str, dict] = {
    # Ranges/symbols below are the plugins' real LV2 control ports (not
    # normalized 0..1 like mda), so slider values map 1:1 onto param_set.
    "reverb": _entry(
        "DIAKOPAD_FX_REVERB",
        "Reverb",
        [
            {"symbol": "decay", "label": "Decay", "unit": "s", "min": 0.1, "max": 10.0, "default": 1.3, "curve": "linear"},
            {"symbol": "dry_level", "label": "Dry", "unit": "%", "min": 0.0, "max": 100.0, "default": 80.0, "curve": "linear"},
            {"symbol": "late_level", "label": "Wet", "unit": "%", "min": 0.0, "max": 100.0, "default": 20.0, "curve": "linear"},
            {"symbol": "delay", "label": "Pre-delay", "unit": "ms", "min": 0.0, "max": 100.0, "default": 4.0, "curve": "linear"},
            {"symbol": "low_cut", "label": "Cut graves", "unit": "Hz", "min": 0.0, "max": 200.0, "default": 4.0, "curve": "linear"},
            {"symbol": "high_cut", "label": "Cut agudos", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 7600.0, "curve": "log"},
            {"symbol": "width", "label": "Largura", "unit": "%", "min": 50.0, "max": 150.0, "default": 100.0, "curve": "linear"},
            {"symbol": "size", "label": "Tamanho", "unit": "m", "min": 10.0, "max": 60.0, "default": 24.0, "curve": "linear"},
        ],
        lv2_uri="https://github.com/michaelwillis/dragonfly-reverb",
        in_ports=DRAGONFLY_IN,
        out_ports=DRAGONFLY_OUT,
    ),
    "reverb_plate": _entry(
        "DIAKOPAD_FX_REVERB_PLATE",
        "Reverb Plate",
        [
            {"symbol": "decay", "label": "Decay", "unit": "s", "min": 0.1, "max": 10.0, "default": 0.4, "curve": "linear"},
            {"symbol": "dry_level", "label": "Dry", "unit": "%", "min": 0.0, "max": 100.0, "default": 80.0, "curve": "linear"},
            {"symbol": "early_level", "label": "Wet", "unit": "%", "min": 0.0, "max": 100.0, "default": 20.0, "curve": "linear"},
            {"symbol": "predelay", "label": "Pre-delay", "unit": "ms", "min": 0.0, "max": 100.0, "default": 0.0, "curve": "linear"},
            {"symbol": "low_cut", "label": "Cut graves", "unit": "Hz", "min": 0.0, "max": 200.0, "default": 200.0, "curve": "linear"},
            {"symbol": "high_cut", "label": "Cut agudos", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 16000.0, "curve": "log"},
            {"symbol": "early_damp", "label": "Damping", "unit": "Hz", "min": 1000.0, "max": 16000.0, "default": 13000.0, "curve": "log"},
        ],
        lv2_uri="urn:dragonfly:plate",
        in_ports=DRAGONFLY_IN,
        out_ports=DRAGONFLY_OUT,
    ),
    "reverb_ir": _entry(
        "DIAKOPAD_FX_REVERB_IR",
        "Reverb IR",
        [
            # ZamVerb: convolution reverb with 7 built-in IRs selected by
            # `room` (0..6, integer). The port is flagged NonAutomatable by
            # the plugin; if mod-host's param_set refuses it on a given
            # build, drop `room` from here and the default IR stays loaded.
            {"symbol": "wetdry", "label": "Mix", "unit": "%", "min": 0.0, "max": 100.0, "default": 50.0, "curve": "linear"},
            {"symbol": "master", "label": "Nivel", "unit": "dB", "min": -30.0, "max": 30.0, "default": 0.0, "curve": "linear"},
            {"symbol": "room", "label": "Sala", "unit": "", "min": 0.0, "max": 6.0, "default": 0.0, "curve": "linear"},
        ],
        lv2_uri="urn:zamaudio:ZamVerb",
        in_ports=DRAGONFLY_IN,
        out_ports=DRAGONFLY_OUT,
    ),
    "chorus": _entry(
        "DIAKOPAD_FX_CHORUS",
        "Chorus",
        [
            {"symbol": "rate", "label": "Velocidade", "unit": "Hz", "min": 0.01, "max": 20.0, "default": 0.25, "curve": "log"},
            {"symbol": "depth", "label": "Profundidade", "unit": "ms", "min": 0.1, "max": 20.0, "default": 5.0, "curve": "linear"},
            {"symbol": "voices", "label": "Vozes", "unit": "", "min": 0.0, "max": 14.0, "default": 2.0, "curve": "linear"},
            {"symbol": "drywet", "label": "Mix", "unit": "%", "min": 0.0, "max": 100.0, "default": 50.0, "curve": "linear"},
        ],
        lv2_uri="http://lsp-plug.in/plugins/lv2/chorus_stereo",
    ),
    "flanger": _entry(
        "DIAKOPAD_FX_FLANGER",
        "Flanger",
        [
            {"symbol": "rate", "label": "Velocidade", "unit": "Hz", "min": 0.01, "max": 20.0, "default": 0.25, "curve": "log"},
            {"symbol": "depth", "label": "Profundidade", "unit": "ms", "min": 0.1, "max": 20.0, "default": 2.0, "curve": "linear"},
            {"symbol": "fb_on", "label": "Feedback", "unit": "", "min": 0.0, "max": 1.0, "default": 1.0, "curve": "linear"},
            {"symbol": "fgain", "label": "Qtd. feedback", "unit": "", "min": 0.0, "max": 0.89, "default": 0.5, "curve": "linear"},
            {"symbol": "drywet", "label": "Mix", "unit": "%", "min": 0.0, "max": 100.0, "default": 50.0, "curve": "linear"},
        ],
        lv2_uri="http://lsp-plug.in/plugins/lv2/flanger_stereo",
    ),
    "pitch": _entry(
        "DIAKOPAD_FX_PITCH",
        "Pitch",
        [
            {"symbol": "blur", "label": "Suavização", "unit": "", "min": 0.0, "max": 0.25, "default": 0.0, "curve": "linear"},
            {"symbol": "window", "label": "Janela", "unit": "", "min": 0.1, "max": 1000.0, "default": 100.0, "curve": "log"},
            {"symbol": "ratio", "label": "Pitch", "unit": "x", "min": 0.25, "max": 4.0, "default": 1.0, "curve": "log"},
            {"symbol": "xfade", "label": "Crossfade", "unit": "", "min": 0.0, "max": 1.0, "default": 1.0, "curve": "linear"},
        ],
        lv2_uri="http://distrho.sf.net/plugins/MaPitchshift",
        in_ports="lv2_audio_in_1",
        out_ports="lv2_audio_out_1,lv2_audio_out_2",
    ),
    "phaser": _entry(
        "DIAKOPAD_FX_PHASER",
        "Phaser",
        [
            {"symbol": "rate", "label": "Velocidade", "unit": "Hz", "min": 0.01, "max": 20.0, "default": 0.25, "curve": "log"},
            {"symbol": "depth", "label": "Profundidade", "unit": "", "min": 0.0, "max": 10.0, "default": 1.0, "curve": "linear"},
            {"symbol": "lfs", "label": "Varre de", "unit": "Hz", "min": 50.0, "max": 20000.0, "default": 200.0, "curve": "log"},
            {"symbol": "lfe", "label": "Varre ate", "unit": "Hz", "min": 50.0, "max": 20000.0, "default": 5000.0, "curve": "log"},
            {"symbol": "drywet", "label": "Mix", "unit": "%", "min": 0.0, "max": 100.0, "default": 50.0, "curve": "linear"},
        ],
        lv2_uri="http://lsp-plug.in/plugins/lv2/phaser_stereo",
    ),
    "delay": _entry(
        "DIAKOPAD_FX_DELAY",
        "Delay",
        [
            {"symbol": "fx_mix", "label": "Mix", "unit": "", "min": 0.0, "max": 1.0, "default": 0.3, "curve": "linear"},
            {"symbol": "l_delay", "label": "Tempo L", "unit": "s", "min": 0.0, "max": 1.0, "default": 0.25, "curve": "linear"},
            {"symbol": "r_delay", "label": "Tempo R", "unit": "s", "min": 0.0, "max": 1.0, "default": 0.25, "curve": "linear"},
            {"symbol": "feedback", "label": "Feedback", "unit": "", "min": 0.0, "max": 0.95, "default": 0.3, "curve": "linear"},
        ],
        lv2_uri="http://drobilla.net/plugins/mda/Delay",
        in_ports="left_in,right_in",
        out_ports="left_out,right_out",
    ),
    "compressor": _entry(
        "DIAKOPAD_FX_COMPRESSOR",
        "Compressor",
        [
            # mda/Dynamics normalizado: thresh 0..1 = -60..0 dB,
            # ratio 0..1 = 1:1..20:1, output 0..1 = -24..+24 dB.
            {"symbol": "thresh", "label": "Threshold", "unit": "", "min": 0.0, "max": 1.0, "default": 0.7, "curve": "linear"},
            {"symbol": "ratio", "label": "Ratio", "unit": "", "min": 0.0, "max": 1.0, "default": 0.16, "curve": "linear"},
            {"symbol": "output", "label": "Makeup", "unit": "", "min": 0.0, "max": 1.0, "default": 0.5, "curve": "linear"},
        ],
        lv2_uri="http://drobilla.net/plugins/mda/Dynamics",
        in_ports="left_in,right_in",
        out_ports="left_out,right_out",
    ),
    "drive": _entry(
        "DIAKOPAD_FX_DRIVE",
        "Overdrive",
        [
            {"symbol": "drive", "label": "Quantidade", "unit": "", "min": 0.0, "max": 1.0, "default": 0.3, "curve": "linear"},
            {"symbol": "muffle", "label": "Tom", "unit": "", "min": 0.0, "max": 1.0, "default": 0.5, "curve": "linear"},
        ],
        lv2_uri="http://drobilla.net/plugins/mda/Overdrive",
        in_ports="left_in,right_in",
        out_ports="left_out,right_out",
    ),
    "eq3": _entry(
        "DIAKOPAD_FX_EQ3",
        "EQ 3 bandas",
        [
            # x42 fil4: lowshelf / mid bell (seção 2) / highshelf em dB.
            {"symbol": "LSgain", "label": "Graves", "unit": "dB", "min": -18, "max": 18, "default": 0, "curve": "linear"},
            {"symbol": "gain2", "label": "Médios", "unit": "dB", "min": -18, "max": 18, "default": 0, "curve": "linear"},
            {"symbol": "HSgain", "label": "Agudos", "unit": "dB", "min": -18, "max": 18, "default": 0, "curve": "linear"},
        ],
        lv2_uri="http://gareus.org/oss/lv2/fil4#stereo",
        in_ports="inL,inR",
        out_ports="outL,outR",
    ),
}


def default_params(plugin_id: str) -> dict[str, float]:
    plugin = PLUGIN_CATALOG.get(plugin_id)
    if not plugin:
        return {}
    return {p["symbol"]: p["default"] for p in plugin["params"]}


def effective_params(plugin_id: str, stored: dict | None) -> dict[str, float]:
    """Plugin defaults overlaid with stored values, dropping symbols that no
    longer belong to the plugin (e.g. a scene saved with mda/Ambience's
    mix/size/hf_damp against today's Dragonfly reverb) and clamping to each
    parameter's LV2 range, so stale rows never reach mod-host's param_set."""
    plugin = PLUGIN_CATALOG.get(plugin_id)
    if not plugin:
        return {}
    result = default_params(plugin_id)
    for p in plugin["params"]:
        value = (stored or {}).get(p["symbol"])
        if value is None:
            continue
        try:
            result[p["symbol"]] = max(float(p["min"]), min(float(p["max"]), float(value)))
        except (TypeError, ValueError):
            continue
    return result


def list_catalog() -> list[dict]:
    """Serializable form for the frontend (GET /api/effects/catalog)."""
    return [{"plugin_id": plugin_id, **entry} for plugin_id, entry in PLUGIN_CATALOG.items()]

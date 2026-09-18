"""Curated catalog of per-pad effect slot plugins.

Deliberately a small, curated set rather than "browse every installed LV2
plugin" - keeps the UI and the mod-host wiring in orchestrator.py simple.
Each entry's LV2 URI and port names are env-overridable (same convention as
the rest of backend/engine/) since the exact plugin available on a given
Zynthian OS install is only knowable on-device via `lv2ls`/`lv2info` - see
deploy/install.sh. The parameter list (symbols/ranges) is NOT env-overridable
since that's a lot of surface area; hand-edit this file once a real plugin
has been picked for a catalog slot on the target device.
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
    reference image (Zynthian OS bookworm aarch64, see README "Achados da
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

    The URI and control symbol vary across Zynthian images, so they are
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


PLUGIN_CATALOG: dict[str, dict] = {
    # MDA plugins expose normalized 0..1 control ports (e.g. Dynamics
    # thresh 0..1 = -60..0 dB internally); the raw ranges are kept honest
    # here so slider values map 1:1 onto param_set values.
    "reverb": _entry(
        "DIAKOPAD_FX_REVERB",
        "Reverb",
        [
            {"symbol": "mix", "label": "Mix", "unit": "", "min": 0.0, "max": 1.0, "default": 0.3, "curve": "linear"},
            {"symbol": "size", "label": "Tamanho", "unit": "", "min": 0.0, "max": 1.0, "default": 0.5, "curve": "linear"},
            {"symbol": "hf_damp", "label": "Damping", "unit": "", "min": 0.0, "max": 1.0, "default": 0.5, "curve": "linear"},
        ],
        lv2_uri="http://drobilla.net/plugins/mda/Ambience",
        in_ports="left_in,right_in",
        out_ports="left_out,right_out",
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


def list_catalog() -> list[dict]:
    """Serializable form for the frontend (GET /api/effects/catalog)."""
    return [{"plugin_id": plugin_id, **entry} for plugin_id, entry in PLUGIN_CATALOG.items()]

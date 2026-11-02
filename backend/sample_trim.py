"""Auto-trim: finds the silence at the start and end of each sample so the
generated .sfz can skip it (`offset` / `end` opcodes) - the audio files are
never modified.

Measured on the laptop (nov/2026): KIT_Reversed_Cymbal_086 had 1.07 s of
digital silence before the swell even began, so the pad seemed to respond a
second late; a few sound effects had ~100 ms. Drum hits start at 0-5 ms.

The cut is conservative: the sound starts at the first moment it rises above
TRIM_DB relative to the sample's own peak (-60 dB: real silence/noise floor
only, a reverse cymbal's quiet crescendo is kept), PRE_ROLL_MS earlier so the
attack is never clipped. The end is cut TAIL_MS after the last moment above
the same threshold.

Results are cached per sample in the samples table (trim_version guards
against threshold changes). WAV int16/24/32 is read with the stdlib `wave`;
anything else (mp3, ogg, flac, float WAV) is decoded with ffmpeg/ffprobe when
available, otherwise the sample is left untrimmed.
"""
from __future__ import annotations

import array
import logging
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Optional

import storage

logger = logging.getLogger("diakopad.sample_trim")

TRIM_DB = float(os.environ.get("DIAKOPAD_TRIM_DB", "-60"))
PRE_ROLL_MS = 2.0
TAIL_MS = 30.0
MIN_LEAD_MS = 5.0   # don't bother with offsets smaller than this
MIN_TAIL_MS = 20.0  # nor with end cuts smaller than this
TRIM_VERSION = 1


def _read_wave(path: Path) -> Optional[tuple[array.array, int, int, float]]:
    """(interleaved samples, channels, sample rate, full scale) or None."""
    try:
        with wave.open(str(path)) as w:
            sw, ch, sr, n = w.getsampwidth(), w.getnchannels(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError):
        return None
    if sw == 2:
        return array.array("h", raw), ch, sr, 32768.0
    if sw == 4:
        return array.array("i", raw), ch, sr, 2147483648.0
    if sw == 3:
        a = array.array("i", (int.from_bytes(raw[i:i + 3], "little", signed=True) for i in range(0, len(raw) - 2, 3)))
        return a, ch, sr, 8388608.0
    if sw == 1:
        return array.array("h", (b - 128 for b in raw)), ch, sr, 128.0
    return None


def _read_ffmpeg(path: Path) -> Optional[tuple[array.array, int, int, float]]:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
             "-of", "default=nw=1", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        info = dict(line.split("=", 1) for line in probe.split() if "=" in line)
        sr, ch = int(info["sample_rate"]), int(info["channels"])
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le", "-"],
            capture_output=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
        logger.warning("could not decode %s for trimming: %s", path.name, exc)
        return None
    data = array.array("f")
    data.frombytes(raw[: len(raw) - len(raw) % 4])
    return data, ch, sr, 1.0


def analyze(path: Path) -> tuple[int, Optional[int]]:
    """(start frame, end frame or None) to play, at the file's own rate."""
    decoded = _read_wave(path) or _read_ffmpeg(path)
    if decoded is None:
        return 0, None
    data, ch, sr, full = decoded
    if not data:
        return 0, None
    peak = max(abs(min(data)), abs(max(data)))
    if peak <= 0:
        return 0, None
    threshold = peak * 10 ** (TRIM_DB / 20)
    first = next((i for i, v in enumerate(data) if abs(v) >= threshold), 0) // ch
    last = len(data) - 1
    while last > 0 and abs(data[last]) < threshold:
        last -= 1
    last //= ch
    frames = len(data) // ch

    start = max(0, first - int(PRE_ROLL_MS * sr / 1000))
    if start < MIN_LEAD_MS * sr / 1000:
        start = 0
    end: Optional[int] = min(frames - 1, last + int(TAIL_MS * sr / 1000))
    if frames - 1 - end < MIN_TAIL_MS * sr / 1000:
        end = None
    return start, end


def trims_for(samples: list[dict], samples_dir: Path) -> dict[int, tuple[int, Optional[int]]]:
    """{sample_id: (start, end)} for the given rows ({"sample_id"/"id",
    "filename"}), analysing (and caching) any sample not analysed yet."""
    ids = {s.get("sample_id", s.get("id")): s["filename"] for s in samples if s.get("filename")}
    ids.pop(None, None)
    cached = storage.get_sample_trims(list(ids), TRIM_VERSION)
    for sample_id, filename in ids.items():
        if sample_id in cached:
            continue
        path = samples_dir / filename
        if not path.is_file():
            continue
        start, end = analyze(path)
        storage.set_sample_trim(sample_id, start, end, TRIM_VERSION)
        cached[sample_id] = (start, end)
        if start or end is not None:
            logger.info("auto-trim %s: start %d frames, end %s", filename, start, end)
    return cached

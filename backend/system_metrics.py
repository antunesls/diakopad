"""Lightweight host metrics exposed through the engine-status endpoint."""
from __future__ import annotations

_previous_cpu_times: tuple[int, int] | None = None


def cpu_percent() -> float | None:
    """Returns total CPU use since the last sample from Linux /proc/stat."""
    global _previous_cpu_times
    try:
        with open("/proc/stat", encoding="ascii") as proc_stat:
            fields = [int(value) for value in proc_stat.readline().split()[1:]]
        total = sum(fields)
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    except (IndexError, OSError, ValueError):
        return None

    previous = _previous_cpu_times
    _previous_cpu_times = (total, idle)
    if previous is None or total <= previous[0]:
        return 0.0
    percent = 100.0 * (1 - (idle - previous[1]) / (total - previous[0]))
    return round(max(0.0, min(100.0, percent)), 1)

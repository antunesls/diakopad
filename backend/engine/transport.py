"""Shared musical timeline for the MIDI engines.

The transport maps monotonic time to musical ticks. Tempo changes re-anchor
the mapping at the current tick, so consumers keep their beat and bar phase.
"""
from __future__ import annotations

import math
import time

TICKS_PER_BEAT = 96


class Transport:
    def __init__(self, bpm: float = 100.0, pulses_per_bar: int = 4) -> None:
        self._bpm = bpm
        self._pulses_per_bar = pulses_per_bar
        self._anchor_time = 0.0
        self._anchor_tick = 0.0
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def start(self, now: float | None = None) -> None:
        if self._running:
            return
        self._anchor_time = time.monotonic() if now is None else now
        self._anchor_tick = 0.0
        self._running = True

    def tick_position(self, now: float | None = None) -> float:
        if not self._running:
            return self._anchor_tick
        moment = time.monotonic() if now is None else now
        return self._anchor_tick + (moment - self._anchor_time) * self._bpm * TICKS_PER_BEAT / 60.0

    def tick_at(self, now: float | None = None) -> int:
        return int(round(self.tick_position(now)))

    def set_bpm(self, bpm: float, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        self._anchor_tick = self.tick_position(moment)
        self._anchor_time = moment
        self._bpm = bpm

    def set_pulses_per_bar(self, pulses: int) -> None:
        self._pulses_per_bar = max(1, pulses)

    def ticks_per_bar(self) -> int:
        return self._pulses_per_bar * TICKS_PER_BEAT

    def next_bar_tick(self, now: float | None = None) -> int:
        position = self.tick_position(now)
        return int(math.ceil(position / self.ticks_per_bar()) * self.ticks_per_bar())

    def next_grid_tick(self, ticks: int, now: float | None = None) -> int:
        position = self.tick_position(now)
        return int(math.ceil(position / ticks) * ticks)

    def seconds_until_tick(self, tick: int, now: float | None = None) -> float:
        return (tick - self.tick_position(now)) * 60.0 / self._bpm / TICKS_PER_BEAT


_transport = Transport()


def shared() -> Transport:
    return _transport


def start() -> None:
    _transport.start()


def is_running() -> bool:
    return _transport.is_running()


def tick_position() -> float:
    return _transport.tick_position()


def tick_at() -> int:
    return _transport.tick_at()


def next_bar_tick() -> int:
    return _transport.next_bar_tick()


def seconds_until_tick(tick: int) -> float:
    return _transport.seconds_until_tick(tick)


def set_bpm(bpm: float) -> None:
    _transport.set_bpm(bpm)


def set_pulses_per_bar(pulses: int) -> None:
    _transport.set_pulses_per_bar(pulses)


def reset() -> None:
    global _transport
    _transport = Transport()

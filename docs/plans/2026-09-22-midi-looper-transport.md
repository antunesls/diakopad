# MIDI Looper Transport Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Make the MIDI looper, sequencer, and metronome share one musical clock, preventing phase drift and late-event bursts.

**Architecture:** A new `engine.transport` owns monotonic-time to musical-tick conversion, tempo changes, and bar boundaries. Each engine schedules from this source; Looper stores events as ticks relative to its bar-based cycle and skips missed events rather than replaying them in a burst.

**Tech Stack:** Python 3, asyncio, mido/JACK, unittest.

---

### Task 1: Shared Transport

**Files:**
- Create: `backend/engine/transport.py`
- Create: `backend/tests/test_transport.py`
- Modify: `backend/engine/tempo.py`

1. Write failing tests for tick position, bar quantization, and continuity when BPM changes.
2. Implement the monotonic Transport and connect `tempo.set()`/Tap Tempo to preserve phase.
3. Run `python -m unittest discover -s tests -p test_transport.py`.

### Task 2: Looper Scheduling

**Files:**
- Modify: `backend/engine/looper.py`
- Modify: `backend/tests/test_engine_regressions.py`

1. Write failing tests for a late cycle not replaying stale events and events retaining their musical position after BPM change.
2. Store event positions as ticks and schedule only the current transport cycle.
3. Run Looper regression tests.

### Task 3: Transport Consumers

**Files:**
- Modify: `backend/engine/sequencer.py`
- Modify: `backend/engine/metronome.py`
- Modify: `backend/tests/test_engine_regressions.py`

1. Write failing tests proving the engines use the shared phase.
2. Schedule ticks/beats from Transport boundaries; preserve independent play/stop controls.
3. Run backend tests.

### Task 4: Verification

**Files:**
- Review all modified files.

1. Run `python -m unittest discover -s tests` in `backend`.
2. Run frontend tests and record any pre-existing failures.
3. Run `git diff --check` and inspect the final diff.

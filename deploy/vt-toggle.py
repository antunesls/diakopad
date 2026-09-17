#!/usr/bin/env python3
"""Toggles between VT1 (zynthian-ui) and VT2 (DiakoPad kiosk) on a fixed
touch gesture: two taps within TAP_WINDOW seconds, both inside the top-right
corner zone of the touchscreen.

Reads the touchscreen device directly via evdev, at the kernel level, so it
keeps receiving events regardless of which VT currently owns the display
(this is what makes the corner-tap toggle work no matter which app is on
screen).

Run as root (needed for `chvt`) via diakopad-vt-toggle.service.

NOTE for deployment: run `python3 -m evdev.evtest` (or `evtest`) on the Pi to
confirm which /dev/input/eventN is the touchscreen, and to read its real
ABS_X/ABS_Y min/max — TOUCH_DEVICE_HINT and CORNER_FRACTION below may need
adjusting to the actual panel.
"""
from __future__ import annotations

import os
import subprocess
import time

import evdev
from evdev import ecodes

TOUCH_DEVICE_HINT = "touch"  # case-insensitive substring match on device name
# On the confirmed device the touch panel enumerates as
# "wch.cn USB2IIC_CTP_CONTROL" (no "touch" in the name), so we also match on
# "ctp" (capacitive touch panel) before falling back to the generic
# ABS_X/ABS_Y capability scan below.
TOUCH_DEVICE_HINTS = ("touch", "ctp")
CORNER_FRACTION = 0.12  # top-right 12% x 12% of the panel counts as the "hot corner"
TAP_WINDOW = 0.6  # seconds between two taps to count as a toggle gesture
# After a toggle fires, ignore taps for this long. Needed because on the real
# device the touch controller keeps re-asserting BTN_TOUCH=1 (without an
# intervening 0) for as long as a finger stays down, which without an edge
# check + cooldown caused 3 VT switches from a single tap-and-hold and left
# Chromium's GL context in a blank/white state from the rapid VT churn.
TOGGLE_COOLDOWN = 1.5

# Confirmed on the real device: zynthian-ui's Xorg session runs on VT2 (VT1
# is just a text getty), so the kiosk uses VT3 instead of the originally
# assumed VT1/VT2 pair.
VT_ZYNTHIAN = 2
VT_KIOSK = 3


def find_touch_device() -> evdev.InputDevice:
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        caps = dev.capabilities()
        has_abs = ecodes.EV_ABS in caps
        name = dev.name.lower()
        if has_abs and any(hint in name for hint in TOUCH_DEVICE_HINTS):
            return dev
    # Fall back to the first device that reports absolute X/Y (some touch
    # controllers don't have "touch" in their reported name).
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        codes = [c for c, _ in dev.capabilities().get(ecodes.EV_ABS, [])]
        if ecodes.ABS_X in codes and ecodes.ABS_Y in codes:
            return dev
    raise RuntimeError("No touchscreen input device found (checked evdev.list_devices()).")


def get_axis_range(dev: evdev.InputDevice, code: int) -> tuple[int, int]:
    info = dev.absinfo(code)
    return info.min, info.max


def current_vt() -> int:
    try:
        with open("/sys/class/tty/tty0/active") as f:
            # e.g. "tty2" -> 2
            return int(f.read().strip().replace("tty", ""))
    except Exception:
        return VT_ZYNTHIAN


def switch_vt(target: int) -> None:
    subprocess.run(["sudo", "chvt", str(target)], check=False)
    if target == VT_KIOSK:
        # The kiosk's Chromium sits on VT3 in the background the whole time
        # and can end up showing a stale/blank frame by the time someone
        # switches to it (observed on the real device: its network service
        # silently restarted itself while backgrounded, and the page never
        # repainted). Forcing a reload right after the switch is a cheap,
        # reliable fix regardless of the exact cause.
        time.sleep(0.3)
        env = {**os.environ, "DISPLAY": ":1"}
        subprocess.run(["xdotool", "key", "F5"], env=env, check=False)


def main() -> None:
    dev = find_touch_device()
    print(f"[vt-toggle] listening on {dev.path} ({dev.name})")

    x_min, x_max = get_axis_range(dev, ecodes.ABS_X)
    y_min, y_max = get_axis_range(dev, ecodes.ABS_Y)
    corner_x = x_min + (x_max - x_min) * (1 - CORNER_FRACTION)
    corner_y = y_min + (y_max - y_min) * CORNER_FRACTION

    last_x = last_y = None
    last_tap_time = 0.0
    last_toggle_time = 0.0
    touch_is_down = False

    for event in dev.read_loop():
        if event.type == ecodes.EV_ABS:
            if event.code == ecodes.ABS_X:
                last_x = event.value
            elif event.code == ecodes.ABS_Y:
                last_y = event.value
        elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
            if event.value == 0:
                touch_is_down = False
                continue
            if event.value != 1 or touch_is_down:
                continue  # not a fresh press (either a release-adjacent value or a repeat while held)
            touch_is_down = True

            now = time.time()
            if now - last_toggle_time < TOGGLE_COOLDOWN:
                continue
            if last_x is None or last_y is None:
                continue
            in_corner = last_x >= corner_x and last_y <= corner_y
            if not in_corner:
                continue
            if now - last_tap_time <= TAP_WINDOW:
                target = VT_KIOSK if current_vt() == VT_ZYNTHIAN else VT_ZYNTHIAN
                print(f"[vt-toggle] double-tap detected, switching to VT{target}")
                switch_vt(target)
                last_toggle_time = now
                last_tap_time = 0.0
            else:
                last_tap_time = now


if __name__ == "__main__":
    main()

#!/bin/bash
# Keeps a Bluetooth MIDI device (e.g. the SMC-PAD paired over BLE) actively
# connected. BLE peripherals routinely drop their GATT connection on their
# own after a period of inactivity/power-saving - being paired, bonded and
# trusted does NOT mean actively connected, and a dropped connection leaves
# the PipeWire MIDI-bridge port sitting in the JACK graph looking "wired"
# while zero bytes actually flow through it (see README's Bluetooth
# section). DiakoPad itself only sees the JACK/PipeWire MIDI graph, never
# the Bluetooth stack, so it can't detect or fix this on its own - this
# script is the reconnect loop BlueZ doesn't provide for MIDI peripherals.
#
# Usage:
#   DIAKOPAD_BT_MAC=AA:BB:CC:DD:EE:FF bash deploy/diakopad-bt-watchdog.sh
#
# Find the address with `bluetoothctl devices` (device must already be
# paired/trusted - this script only reconnects, it doesn't pair).
set -euo pipefail

MAC="${DIAKOPAD_BT_MAC:?Set DIAKOPAD_BT_MAC to the device address from: bluetoothctl devices}"
INTERVAL="${DIAKOPAD_BT_WATCHDOG_INTERVAL:-5}"
# bluetoothctl connect can hang for a long time (tens of seconds) when the
# device isn't currently reachable/advertising - bounding it keeps retries
# close to INTERVAL instead of stalling the whole loop on one slow attempt.
CONNECT_TIMEOUT="${DIAKOPAD_BT_WATCHDOG_CONNECT_TIMEOUT:-8}"

echo "==> Watching $MAC every ${INTERVAL}s, reconnecting when it drops"
while true; do
  if ! bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
    echo "$(date '+%H:%M:%S') $MAC not connected - reconnecting..."
    timeout "$CONNECT_TIMEOUT" bluetoothctl connect "$MAC" >/dev/null 2>&1 || true
  fi
  sleep "$INTERVAL"
done

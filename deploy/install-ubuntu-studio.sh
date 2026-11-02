#!/bin/bash
# Installs DiakoPad on one of two targets (same script, same files):
#
#   laptop       Ubuntu Studio (or any desktop Debian/Ubuntu with a
#                JACK-compatible audio server). PipeWire/JACK comes up with
#                the graphical login and Firefox opens the UI in kiosk mode.
#   pi-headless  Raspberry Pi 4/5 with Raspberry Pi OS Lite 64-bit, no
#                monitor. PipeWire + DiakoPad start at boot without a login
#                (linger) and the UI is used remotely from another device's
#                browser at http://<hostname>.local:8080. Tuned for the
#                SMC-PAD over USB MIDI.
#
# The target is picked automatically (Raspberry Pi without a graphical boot
# target -> pi-headless, everything else -> laptop); force it with
# DIAKOPAD_TARGET=laptop or DIAKOPAD_TARGET=pi-headless.
#
# The audio engine uses one sfizz_jack instance per pad and mod-host for
# effects:
#
#   * DiakoPad runs as your normal user because PipeWire/JACK runs in the
#     same user session.
#   * sfizz_jack and mod-host are usually not packaged by apt and are built
#     from source below.
#
# Run from the root of this repo (a checkout on the target itself, or after
# `scp -r`/`rsync` from another machine), as your normal user with sudo
# available for the apt-get/build-dep steps.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${DIAKOPAD_INSTALL_DIR:-$HOME/diakopad}"
SFZ_BANK_DIR="$INSTALL_DIR/backend/sfz_bank"
BUILD_DIR="${DIAKOPAD_BUILD_DIR:-$HOME/src/diakopad-audio-deps}"

if [ "$(id -u)" -eq 0 ]; then
  echo "!!  Run this WITHOUT sudo, as your normal user (it calls sudo itself)." >&2
  exit 1
fi

detect_target() {
  if [ -n "${DIAKOPAD_TARGET:-}" ]; then
    echo "$DIAKOPAD_TARGET"
  elif grep -qa "Raspberry Pi" /proc/device-tree/model 2>/dev/null \
      && [ "$(systemctl get-default 2>/dev/null)" != "graphical.target" ]; then
    echo "pi-headless"
  else
    echo "laptop"
  fi
}

TARGET="$(detect_target)"
case "$TARGET" in
  laptop) QUANTUM="${DIAKOPAD_QUANTUM:-128}" ;;
  pi-headless)
    # 128 frames is too tight for 16 sfizz_jack + mod-host on a Pi 4 and
    # shows up as xruns; 256 frames (~5.8ms @ 44.1kHz) is still fine to play.
    QUANTUM="${DIAKOPAD_QUANTUM:-256}"
    if [ "$(uname -m)" != "aarch64" ]; then
      echo "!!  pi-headless needs a 64-bit OS (uname -m is $(uname -m))." \
           "Flash Raspberry Pi OS Lite (64-bit)." >&2
      exit 1
    fi
    ;;
  *)
    echo "!!  Unknown DIAKOPAD_TARGET=$TARGET (use laptop or pi-headless)." >&2
    exit 1
    ;;
esac
echo "==> Target: $TARGET (PipeWire quantum $QUANTUM)"

# Raspberry Pi OS Lite doesn't ship rsync, needed right below.
command -v rsync >/dev/null 2>&1 || { sudo apt-get update; sudo apt-get install -y rsync; }

if [ "$REPO_DIR" != "$INSTALL_DIR" ]; then
  echo "==> Copying app to $INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  rsync -a --delete \
    --exclude ".git" --exclude ".venv" --exclude "diakopad.db" \
    --exclude "samples" --exclude "sfz_bank" --exclude "generated" \
    --exclude "__pycache__" --exclude ".pytest_cache" \
    "$REPO_DIR"/ "$INSTALL_DIR"/
else
  echo "==> Running in-place in $INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR/backend/samples" "$SFZ_BANK_DIR" "$BUILD_DIR"

# Stop any previous install's service so rebuilding sfizz_jack/mod-host below
# doesn't hit "Text file busy" trying to overwrite a binary its own running
# process still has mapped (harmless no-op on a first install).
systemctl --user stop diakopad.service 2>/dev/null || true

# Installs the first package name apt knows about. Debian (Raspberry Pi OS)
# and Ubuntu don't always agree on LV2 plugin package names; a missing
# plugin only disables that effect, so warn instead of aborting.
install_first_available() {
  local pkg
  for pkg in "$@"; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
      sudo apt-get install -y "$pkg"
      return 0
    fi
  done
  echo "!!  None of these packages is available, skipping: $*" >&2
}

echo "==> Installing build/runtime dependencies"
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-pip \
  build-essential cmake git pkg-config curl \
  libjack-jackd2-dev liblilv-dev lv2-dev libreadline-dev \
  lilv-utils avahi-utils
install_first_available mda-lv2
install_first_available x42-plugins
install_first_available dragonfly-reverb dragonfly-reverb-lv2
install_first_available lsp-plugins lsp-plugins-lv2
install_first_available zam-plugins

if [ "$TARGET" = "pi-headless" ]; then
  # Raspberry Pi OS Lite has no audio server. dbus-user-session gives the
  # lingering user manager a session bus, which WirePlumber needs to reserve
  # the ALSA devices without anyone logged in.
  sudo apt-get install -y \
    pipewire pipewire-jack wireplumber rtkit dbus-user-session alsa-utils
fi

echo "==> Setting up Python venv"
python3 -m venv "$INSTALL_DIR/backend/.venv"
"$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

# Compiling sfizz with one job per core runs a 1-2 GB Pi out of memory.
MEM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
JOBS="$(nproc)"
if [ "$MEM_KB" -lt 3000000 ]; then
  JOBS=2
fi

if ! command -v sfizz_jack >/dev/null 2>&1; then
  echo "==> Building sfizz (with its JACK client) from source (-j$JOBS)"
  if [ ! -d "$BUILD_DIR/sfizz" ]; then
    git clone --recursive https://github.com/sfztools/sfizz.git "$BUILD_DIR/sfizz"
  fi
  # Upstream's sfizz_jack starts an interactive CLI thread
  # (clients/jack_client.cpp) that blocks on std::getline(std::cin, ...) for
  # commands. Under systemd/SSH stdin has no terminal and is already at EOF,
  # so that thread immediately sets shouldClose=true and the 1-second main
  # loop tears the whole client down within ~1s of every spawn - looks like
  # sfizz "crashing" in a tight loop (validated on-device, Ubuntu Studio,
  # Sep/2026). Strip the thread before building the desktop binary.
  sed -i '/std::thread cli_thread(cliThreadProc);/d; /cli_thread\.join();/d' \
    "$BUILD_DIR/sfizz/clients/jack_client.cpp"
  cmake -S "$BUILD_DIR/sfizz" -B "$BUILD_DIR/sfizz/build" \
    -DCMAKE_BUILD_TYPE=Release -DSFIZZ_JACK=ON -DSFIZZ_LV2=OFF -DSFIZZ_VST=OFF
  cmake --build "$BUILD_DIR/sfizz/build" -j"$JOBS"
  sudo cmake --install "$BUILD_DIR/sfizz/build"
  sudo ldconfig
else
  echo "==> sfizz_jack already on PATH, skipping build"
fi

if ! command -v mod-host >/dev/null 2>&1; then
  echo "==> Building mod-host from source (-j$JOBS)"
  if [ ! -d "$BUILD_DIR/mod-host" ]; then
    git clone https://github.com/moddevices/mod-host.git "$BUILD_DIR/mod-host"
  fi
  make -C "$BUILD_DIR/mod-host" -j"$JOBS"
  sudo make -C "$BUILD_DIR/mod-host" install
else
  echo "==> mod-host already on PATH, skipping build"
fi

echo "==> Installing systemd --user unit"
mkdir -p "$HOME/.config/systemd/user"
UNIT_SED=(-e "s#__INSTALL_DIR__#$INSTALL_DIR#g")
if [ "$TARGET" = "pi-headless" ]; then
  # No local screen: the UI is only reachable over the network, so listen on
  # all interfaces and require a token (kept across reinstalls so phones and
  # tablets that already have the cookie keep working).
  TOKEN_FILE="$HOME/.config/diakopad/token"
  if [ ! -s "$TOKEN_FILE" ]; then
    mkdir -p "$(dirname "$TOKEN_FILE")"
    (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(16))' > "$TOKEN_FILE")
  fi
  DIAKOPAD_TOKEN_VALUE="$(cat "$TOKEN_FILE")"
  UNIT_SED+=(
    -e "s#^Environment=DIAKOPAD_HOST=.*#Environment=DIAKOPAD_HOST=0.0.0.0#"
    -e "s#^\#Environment=DIAKOPAD_TOKEN=.*#Environment=DIAKOPAD_TOKEN=$DIAKOPAD_TOKEN_VALUE#"
  )
fi
sed "${UNIT_SED[@]}" "$INSTALL_DIR/deploy/diakopad-desktop.service" \
  > "$HOME/.config/systemd/user/diakopad.service"
chmod 600 "$HOME/.config/systemd/user/diakopad.service"
cp "$INSTALL_DIR/deploy/diakopad-restart.service" "$HOME/.config/systemd/user/diakopad-restart.service"
systemctl --user daemon-reload

echo "==> Pinning PipeWire to 44100Hz / $QUANTUM frames"
mkdir -p "$HOME/.config/pipewire/pipewire.conf.d"
sed "s#__QUANTUM__#$QUANTUM#g" "$INSTALL_DIR/deploy/pipewire-clock.conf" \
  > "$HOME/.config/pipewire/pipewire.conf.d/99-diakopad-clock.conf"

setup_laptop_session() {
  echo "==> Installing auto-open-browser-on-login (opens http://localhost:8080 once the desktop session starts)"
  mkdir -p "$HOME/.local/bin" "$HOME/.config/autostart"
  cp "$INSTALL_DIR/deploy/diakopad-open-browser.sh" "$HOME/.local/bin/diakopad-open-browser.sh"
  chmod +x "$HOME/.local/bin/diakopad-open-browser.sh"
  sed "s#__HOME__#$HOME#g" "$INSTALL_DIR/deploy/diakopad-browser.desktop" \
    > "$HOME/.config/autostart/diakopad-browser.desktop"
}

# Enables an I2S DAC HAT (default: PCM5122 boards via hifiberry-dacplus) and
# turns off the onboard jack/HDMI audio, so the DAC is the only - and thus
# default - PipeWire sink. DIAKOPAD_PI_DAC_OVERLAY picks another overlay
# (e.g. iqaudio-dacplus, allo-boss-dac-pcm512x-audio, hifiberry-dacplus-std);
# "none" leaves config.txt alone (USB audio interface instead).
setup_pi_dac() {
  local overlay="${DIAKOPAD_PI_DAC_OVERLAY:-hifiberry-dacplus}"
  [ "$overlay" = "none" ] && return 0

  local config=/boot/firmware/config.txt
  [ -f "$config" ] || config=/boot/config.txt
  if [ ! -f "$config" ]; then
    echo "!!  config.txt not found; enable dtoverlay=$overlay by hand" >&2
    return 0
  fi

  echo "==> I2S DAC: dtoverlay=$overlay, onboard/HDMI audio off ($config)"
  sudo cp -n "$config" "$config.diakopad-bak"
  sudo sed -i \
    -e '/^# BEGIN DiakoPad audio/,/^# END DiakoPad audio/d' \
    -e 's/^dtparam=audio=on/dtparam=audio=off/' \
    -e 's/^dtoverlay=vc4-kms-v3d$/dtoverlay=vc4-kms-v3d,noaudio/' \
    "$config"
  sudo tee -a "$config" >/dev/null <<EOF
# BEGIN DiakoPad audio (managed by deploy/install-ubuntu-studio.sh)
[all]
dtoverlay=$overlay
# END DiakoPad audio
EOF
}

setup_pi_headless() {
  echo "==> Routing libjack to PipeWire (sfizz_jack, mod-host and JACK-Client)"
  local conf found=0
  for conf in /usr/share/doc/pipewire/examples/ld.so.conf.d/pipewire-jack-*.conf; do
    [ -f "$conf" ] || continue
    sudo cp "$conf" /etc/ld.so.conf.d/
    found=1
  done
  if [ "$found" = 1 ]; then
    sudo ldconfig
  else
    echo "!!  pipewire-jack ld.so.conf example not found; run DiakoPad under pw-jack" >&2
  fi

  echo "==> Starting PipeWire + DiakoPad at boot without a login (linger)"
  sudo usermod -aG audio "$USER"
  sudo loginctl enable-linger "$USER"
  systemctl --user enable pipewire.socket wireplumber.service 2>/dev/null || true

  echo "==> USB MIDI: load the ALSA sequencer and USB audio/MIDI drivers at boot"
  sudo tee /etc/modules-load.d/diakopad-midi.conf >/dev/null <<'EOF'
# Managed by DiakoPad deploy/install-ubuntu-studio.sh (pi-headless).
# PipeWire's Midi-Bridge (where the SMC-PAD shows up) sits on the ALSA
# sequencer, so make sure it exists from boot on.
snd-seq
snd-usb-audio
EOF

  # USB autosuspend can put an idle controller to sleep and swallow the
  # first hits after a pause - keep USB devices always on.
  local cmdline=/boot/firmware/cmdline.txt
  [ -f "$cmdline" ] || cmdline=/boot/cmdline.txt
  if [ -f "$cmdline" ] && ! grep -q "usbcore.autosuspend=" "$cmdline"; then
    echo "==> Disabling USB autosuspend ($cmdline)"
    sudo sed -i '1 s/$/ usbcore.autosuspend=-1/' "$cmdline"
  fi

  setup_pi_dac

  echo "==> Pinning the CPU governor to performance"
  sudo cp "$INSTALL_DIR/deploy/diakopad-cpu-performance.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now diakopad-cpu-performance.service
}

if [ "$TARGET" = "pi-headless" ]; then
  setup_pi_headless
else
  setup_laptop_session
fi

if [ "$TARGET" = "pi-headless" ]; then
  cat <<EOF

==> Base install done (pi-headless). Finish with:

  1. Enable DiakoPad and reboot (DAC overlay, USB and linger changes need it):
         systemctl --user enable diakopad.service
         sudo reboot

  2. After the reboot, WITHOUT logging in locally, check over SSH:
         systemctl --user status pipewire wireplumber diakopad
         pw-metadata -n settings | grep clock   # rate=44100 quantum=$QUANTUM
         aplay -l                               # the I2S DAC (sndrpihifiberry) shows up?
         amixer -c sndrpihifiberry sset Digital 0dB && sudo alsactl store
         wpctl status                           # DAC as default sink?
         wpctl set-default <id>                 # if not
         jack_lsp | grep -i -E "sinco|smc-pad"  # SMC-PAD Midi-Bridge port

  3. On a phone, tablet or laptop on the same network, open ONCE:
         http://$(hostname).local:8080/?token=$DIAKOPAD_TOKEN_VALUE
     (the token becomes a cookie; afterwards http://$(hostname).local:8080/
     is enough). The token is stored in ~/.config/diakopad/token.
     Assign a sound to a pad and hit it on the SMC-PAD.
EOF
else
  cat <<EOF

==> Base install done, but NOT started yet - do these manual steps first
    (see README.md for the Ubuntu Studio setup and LV2 discovery):

  1. Make sure a JACK-compatible server is running in YOUR session before
     starting DiakoPad - either real jackd2 (via qjackctl / Ubuntu Studio
     Controls) or pipewire-jack. Sanity-check:
         jack_lsp || echo "no JACK server reachable yet"
     If PipeWire was already running before this install, restart it so it
     picks up the pinned 44100Hz/$QUANTUM-frame clock (deploy/diakopad-restart.service
     does this same restart, minus the DiakoPad service, if you'd rather use it):
         systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service
         pw-metadata -n settings | grep clock   # confirm rate=44100 quantum=$QUANTUM

  2. The effect catalog's defaults (backend/engine/effects_catalog.py) and
     the master gain in the installed service already target this exact
     setup - validated on-device (Ubuntu Studio, set/2026): Dragonfly
     Hall/Plate, ZamVerb, LSP chorus/flanger/phaser, mda delay/compressor/
     drive, x42 fil4 EQ + x42 balance as master trim. Only re-check if your
     install differs:
          lv2ls | grep -Ei 'dragonfly|zamaudio|lsp-plug|drobilla|gareus'
          lv2info <uri>    # port/param symbols, if you swap a plugin
     Per-entry overrides stay available via DIAKOPAD_FX_<NAME>_LV2_URI (see
     the installed ~/.config/systemd/user/diakopad.service comments).

  3. Start DiakoPad:
         systemctl --user enable --now diakopad.service
         systemctl --user status diakopad.service
     Open http://localhost:8080/, assign a sound to a pad and confirm audio
     comes out (or check 'jack_lsp -c' for the graph wired by
     backend/engine/jackgraph.py).

  4. Optional: 'bash deploy/setup-autoboot.sh' to go from power-on straight
     to DiakoPad (SDDM autologin + Firefox kiosk) - see README.

  5. Optional: 'loginctl enable-linger \$USER' so the user service can start
     at boot without an interactive login session.
EOF
fi

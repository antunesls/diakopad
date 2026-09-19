#!/bin/bash
# Installs DiakoPad on a laptop running Ubuntu Studio (or any desktop
# Debian/Ubuntu with a JACK-compatible audio server), as an alternative to
# the Raspberry Pi/Zynthian OS deploy (see install.sh). Same audio engine
# (one sfizz_jack instance per pad + mod-host for effects, see
# backend/engine/) - only the OS-level plumbing differs:
#
#   * No root: jackd/pipewire-jack run in YOUR user session here, not as a
#     root system service like on the Zynthian image, so DiakoPad also runs
#     as your own user (see deploy/diakopad-desktop.service) - do NOT reuse
#     deploy/diakopad-runtime.conf, that forces User=root for the Pi's
#     UID-scoped JACK shared memory, which does not apply here.
#   * No kiosk (deploy/diakopad-kiosk.service, kiosk-xinitrc) - skip both,
#     this is a normal desktop, just open the URL in a browser.
#   * sfizz_jack and mod-host are usually not packaged by apt and are built
#     from source below.
#
# Run from the root of this repo (a checkout on the laptop itself, or after
# `scp -r`/`rsync` from another machine), as your normal user with sudo
# available for the apt-get/build-dep steps.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${DIAKOPAD_INSTALL_DIR:-$HOME/diakopad}"
SFZ_BANK_DIR="$INSTALL_DIR/backend/sfz_bank"
BUILD_DIR="${DIAKOPAD_BUILD_DIR:-$HOME/src/diakopad-audio-deps}"

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

echo "==> Installing build/runtime dependencies"
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-pip \
  build-essential cmake git pkg-config \
  libjack-jackd2-dev liblilv-dev lv2-dev libreadline-dev \
  mda-lv2 x42-plugins

echo "==> Setting up Python venv"
python3 -m venv "$INSTALL_DIR/backend/.venv"
"$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

if ! command -v sfizz_jack >/dev/null 2>&1; then
  echo "==> Building sfizz (with its JACK client) from source"
  if [ ! -d "$BUILD_DIR/sfizz" ]; then
    git clone --recursive https://github.com/sfztools/sfizz.git "$BUILD_DIR/sfizz"
  fi
  cmake -S "$BUILD_DIR/sfizz" -B "$BUILD_DIR/sfizz/build" \
    -DCMAKE_BUILD_TYPE=Release -DSFIZZ_JACK=ON -DSFIZZ_LV2=OFF -DSFIZZ_VST=OFF
  cmake --build "$BUILD_DIR/sfizz/build" -j"$(nproc)"
  sudo cmake --install "$BUILD_DIR/sfizz/build"
  sudo ldconfig
else
  echo "==> sfizz_jack already on PATH, skipping build"
fi

if ! command -v mod-host >/dev/null 2>&1; then
  echo "==> Building mod-host from source"
  if [ ! -d "$BUILD_DIR/mod-host" ]; then
    git clone https://github.com/moddevices/mod-host.git "$BUILD_DIR/mod-host"
  fi
  make -C "$BUILD_DIR/mod-host" -j"$(nproc)"
  sudo make -C "$BUILD_DIR/mod-host" install
else
  echo "==> mod-host already on PATH, skipping build"
fi

echo "==> Installing systemd --user unit"
mkdir -p "$HOME/.config/systemd/user"
sed "s#__INSTALL_DIR__#$INSTALL_DIR#g" "$INSTALL_DIR/deploy/diakopad-desktop.service" \
  > "$HOME/.config/systemd/user/diakopad.service"
systemctl --user daemon-reload

cat <<EOF

==> Base install done, but NOT started yet - do these manual steps first
    (see README.md for the Ubuntu Studio section and the general LV2
    discovery rationale shared with the Pi deploy):

  1. Make sure a JACK-compatible server is running in YOUR session before
     starting DiakoPad - either real jackd2 (via qjackctl / Ubuntu Studio
     Controls) or pipewire-jack. Sanity-check:
         jack_lsp || echo "no JACK server reachable yet"

  2. Find the real LV2 plugin URIs on this machine and set them in
       ~/.config/systemd/user/diakopad.service (edit, then
       'systemctl --user daemon-reload') - one DIAKOPAD_FX_<NAME>_LV2_URI
      per entry in backend/engine/effects_catalog.py's PLUGIN_CATALOG
      (reverb, delay, compressor, drive, eq3), plus
      DIAKOPAD_FX_MASTER_GAIN_LV2_URI for master volume/mute/PANIC:
          which sfizz_jack mod-host
          lv2ls | grep -i reverb
          lv2ls | grep -i delay
          lv2info <a-uri-from-above>
      Unlike the Pi image, there is no known-good LV2_PATH/URI set to
      start from here - this discovery step is required, not optional.

  3. Start DiakoPad:
         systemctl --user enable --now diakopad.service
         systemctl --user status diakopad.service
     Open http://localhost:8080/, assign a sound to a pad and confirm audio
     comes out (or check 'jack_lsp -c' for the graph wired by
     backend/engine/jackgraph.py).

  4. Optional: 'loginctl enable-linger \$USER' so the user service can start
     at boot without an interactive login session.
EOF

#!/bin/bash
# Installs DiakoPad on a laptop running Ubuntu Studio (or any desktop
# Debian/Ubuntu with a JACK-compatible audio server). The audio engine uses
# one sfizz_jack instance per pad and mod-host for effects.
#
#   * DiakoPad runs as your normal user because PipeWire/JACK runs in the
#     same desktop session.
#   * Use the browser in the desktop session for the presentation interface.
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

# Stop any previous install's service so rebuilding sfizz_jack/mod-host below
# doesn't hit "Text file busy" trying to overwrite a binary its own running
# process still has mapped (harmless no-op on a first install).
systemctl --user stop diakopad.service 2>/dev/null || true

echo "==> Installing build/runtime dependencies"
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-pip \
  build-essential cmake git pkg-config \
  libjack-jackd2-dev liblilv-dev lv2-dev libreadline-dev \
  lilv-utils \
  mda-lv2 x42-plugins \
  dragonfly-reverb lsp-plugins zam-plugins

echo "==> Setting up Python venv"
python3 -m venv "$INSTALL_DIR/backend/.venv"
"$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

if ! command -v sfizz_jack >/dev/null 2>&1; then
  echo "==> Building sfizz (with its JACK client) from source"
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
cp "$INSTALL_DIR/deploy/diakopad-restart.service" "$HOME/.config/systemd/user/diakopad-restart.service"
systemctl --user daemon-reload

cat <<EOF

==> Base install done, but NOT started yet - do these manual steps first
    (see README.md for the Ubuntu Studio setup and LV2 discovery):

  1. Make sure a JACK-compatible server is running in YOUR session before
     starting DiakoPad - either real jackd2 (via qjackctl / Ubuntu Studio
     Controls) or pipewire-jack. Sanity-check:
         jack_lsp || echo "no JACK server reachable yet"

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

  4. Optional: 'loginctl enable-linger \$USER' so the user service can start
     at boot without an interactive login session.
EOF

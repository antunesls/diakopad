#!/bin/bash
# Installs DiakoPad on a Zynthian OS Raspberry Pi, standalone: DiakoPad owns
# the audio engine directly (one sfizz instance per pad + mod-host for
# reverb/delay, see backend/engine/) instead of riding on zynthian-ui's own
# Sfizz chain. Run ON THE PI, from the root of this repo (e.g. after
# `scp -r diakopad zynthian@<ip>:~/` or a git clone), as the `zynthian` user
# with sudo available.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/home/zynthian/diakopad"
SFZ_BANK_DIR="$INSTALL_DIR/backend/sfz_bank"

echo "==> Copying app to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude ".venv" --exclude "diakopad.db" --exclude "samples" --exclude "sfz_bank" \
  "$REPO_DIR"/ "$INSTALL_DIR"/

mkdir -p "$INSTALL_DIR/backend/samples"

echo "==> Setting up Python venv"
python3 -m venv "$INSTALL_DIR/backend/.venv"
"$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

echo "==> Installing per-pad effect plugins (mda suite for the catalog)"
sudo apt-get install -y mda-lv2

echo "==> Preparing DiakoPad's own sfz bank dir: $SFZ_BANK_DIR"
mkdir -p "$SFZ_BANK_DIR"

echo "==> Installing systemd units"
sudo cp "$INSTALL_DIR/deploy/diakopad.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/deploy/diakopad-kiosk.service" /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/diakopad.service.d
sudo cp "$INSTALL_DIR/deploy/diakopad-master.conf" /etc/systemd/system/diakopad.service.d/master.conf
sudo cp "$INSTALL_DIR/deploy/diakopad-runtime.conf" /etc/systemd/system/diakopad.service.d/runtime.conf
chmod +x "$INSTALL_DIR/deploy/kiosk-xinitrc"

sudo systemctl daemon-reload

cat <<'EOF'

==> Systemd units installed, but NOT started yet - do these manual steps
    first (see README.md for the full rationale):

  1. Confirm jackd/a2jmidid still come up without zynthian-ui/webconf
     managing them, THEN disable those two (exact unit names vary by image
     - check first):
         systemctl list-unit-files | grep -i zynth
         sudo systemctl disable --now zynthian.service zynthian-webconf.service
         sudo systemctl status jack2.service a2jmidid.service
     If jackd does NOT come back on its own, do not proceed until you've
     restored it (e.g. with its own dedicated unit) - diakopad.service
     requires jack2.service and a2jmidid.service to start.

  2. Find the real sfizz_jack and mod-host binaries and LV2 plugin URIs
      installed on this image, then set them in
       /etc/systemd/system/diakopad.service (edit the file, then
       `sudo systemctl daemon-reload`) - one DIAKOPAD_FX_<NAME>_LV2_URI per
      entry in backend/engine/effects_catalog.py's PLUGIN_CATALOG (reverb,
      delay, compressor, drive, eq3), plus DIAKOPAD_FX_MASTER_GAIN_LV2_URI
      for the master volume/mute/PANIC control:
          which sfizz_jack mod-host
          lv2ls | grep -i reverb
          lv2ls | grep -i delay
          lv2ls | grep -i compressor
          lv2info <a-uri-from-above>    # to read its exact port/param symbols;
                                         # if they differ from
                                         # effects_catalog.py's placeholders,
                                         # either set DIAKOPAD_FX_<NAME>_IN_PORTS/
                                         # _OUT_PORTS or hand-edit that file

      Notes validated on-device (Zynthian OS bookworm, kernel 6.12, aarch64):
      * The master drop-in (diakopad-master.conf) ships the lvtk "Volume"
        plugin (http://lvtk.org/plugins/volume, symbol "volume", dB) found
        via lv2ls - only override it if your image differs.
      * The runtime drop-in (diakopad-runtime.conf) runs the app as root
        because jackd runs as root and JACK's shared memory is UID-scoped.
      * If mod-host replies with garbage ("not found" floods / resp parse
        errors / crashes), rebuild it from the image's own sources:
            cd /zynthian/zynthian-sw/mod-host && sudo make && sudo make install
      * The venv must have JACK-Client installed (pip line above); if the
        service logs "No module named 'jack'", rerun the pip install.

  3. Start DiakoPad:
         sudo systemctl enable --now diakopad.service
         sudo systemctl status diakopad.service
     Open http://<ip-do-pi>:8080/, atribua um som a um pad e confirme que
     sai áudio (toque o pad físico ou veja `jack_lsp -c` para conferir o
     grafo montado por backend/engine/).

  4. Only after step 3 works, install the kiosk:
         sudo apt-get install -y chromium
         sudo systemctl enable --now diakopad-kiosk.service
EOF

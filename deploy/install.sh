#!/bin/bash
# Installs DiakoPad on a Zynthian Raspberry Pi. Run ON THE PI, from the root
# of this repo (e.g. after `scp -r diakopad zynthian@<ip>:~/` or a git clone),
# as the `zynthian` user with sudo available.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/home/zynthian/diakopad"
SFZ_BANK_DIR="/zynthian/zynthian-my-data/presets/sfizz/DiakoPad"

echo "==> Copying app to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude ".venv" --exclude "diakopad.db" --exclude "samples" \
  "$REPO_DIR"/ "$INSTALL_DIR"/

mkdir -p "$INSTALL_DIR/backend/samples"

echo "==> Setting up Python venv"
python3 -m venv "$INSTALL_DIR/backend/.venv"
"$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

echo "==> Preparing Sfizz preset bank dir: $SFZ_BANK_DIR"
mkdir -p "$SFZ_BANK_DIR"

echo "==> Installing systemd units"
sudo cp "$INSTALL_DIR/deploy/diakopad.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/deploy/diakopad-kiosk.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/deploy/diakopad-vt-toggle.service" /etc/systemd/system/
chmod +x "$INSTALL_DIR/deploy/kiosk-xinitrc" "$INSTALL_DIR/deploy/vt-toggle.py"

sudo systemctl daemon-reload
sudo systemctl enable --now diakopad.service

cat <<'EOF'

==> DiakoPad web app installed and started (diakopad.service).
    Check it: sudo systemctl status diakopad.service
    Open:     http://<ip-do-zynthian>:8080/

Passos manuais restantes (ver README.md):
  1. Na UI nativa do Zynthian, crie uma chain com a engine Sfizz, MIDI
     input = SMC-PAD, canal MIDI 10 (ou o valor de DIAKOPAD_MIDI_CHANNEL
     em diakopad.service).
  2. Em Library > Presets & Soundfonts, aponte o banco de presets do Sfizz
     dessa chain para:
         /zynthian/zynthian-my-data/presets/sfizz/DiakoPad
     Os arquivos diakopad_a.sfz / diakopad_b.sfz vão aparecer aí assim que
     o primeiro pad for atribuído pelo DiakoPad.
  3. Conecte a porta MIDI virtual "DiakoPad" (criada pelo backend) na
     entrada do roteador MIDI do Zynthian, por exemplo:
         aconnect -l                     # confira os nomes/portas exatos
         aconnect 'DiakoPad' 'ZynMidiRouter'
     Considere adicionar esse aconnect a um script de boot para persistir
     entre reinícios.
  4. Só depois de validar o passo 1-3, habilite o kiosk e o daemon de
     alternância de tela (opcional, requer xinit/chromium-browser/python3-evdev
     instalados e o stack gráfico do zynthian-ui confirmado — ver README.md):
         sudo systemctl enable --now diakopad-kiosk.service
         sudo systemctl enable --now diakopad-vt-toggle.service
EOF

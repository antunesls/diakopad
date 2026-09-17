#!/bin/bash
# Installs DiakoPad on a Zynthian Raspberry Pi. Run ON THE PI, from the root
# of this repo (e.g. after `scp -r diakopad zynthian@<ip>:~/` or a git clone),
# as the `zynthian` user with sudo available.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/home/zynthian/diakopad"
SFZ_BANK_DIR="/zynthian/zynthian-my-data/soundfonts/sfz/DiakoPad"

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
  2. Em Library > Presets & Soundfonts, selecione a engine "Sfizz: SFZ" e
     abra "SD> User" — o banco "DiakoPad" (com os presets diakopad_a /
     diakopad_b) aparece automaticamente ali, pois é escaneado direto de:
         /zynthian/zynthian-my-data/soundfonts/sfz/DiakoPad
     Selecione um desses presets uma vez na chain para a engine carregá-lo;
     as trocas seguintes (A/B) são feitas via Program Change pelo DiakoPad.
  3. O roteamento MIDI deste Zynthian é feito via JACK (cliente
     "ZynMidiRouter", portas dev0_in..dev23_in), com uma ponte ALSA->JACK
     (a2j) ativa. A porta virtual ALSA "DiakoPad" criada pelo backend
     aparece do lado JACK como "a2j:DiakoPad [...] (playback)". Conecte-a a
     um slot devN_in livre do ZynMidiRouter, por exemplo:
         jack_lsp -A | grep -A2 DiakoPad      # confira o nome exato da porta
         jack_lsp -c ZynMidiRouter:dev1_in    # cheque se dev1 já está em uso
         jack_connect 'a2j:DiakoPad [128] (playback): DiakoPad' 'ZynMidiRouter:dev1_in'
     Depois, na UI nativa do Zynthian (Hardware/MIDI devices), confirme que
     esse device está com canal "All" ou canal 10, para o Program Change
     (que já carrega o canal na própria mensagem) passar sem ser filtrado.
     Isso precisa ser refeito a cada boot (JACK não persiste conexões) —
     considere um script de systemd rodando após o jackd subir.
  4. Só depois de validar o passo 1-3, instale o Chromium e habilite o
     kiosk + o daemon de alternância de tela (a UI nativa do zynthian roda
     na VT2 neste device, então o kiosk usa a VT3 — já ajustado nos units):
         sudo apt-get install -y chromium
         sudo systemctl enable --now diakopad-kiosk.service
         sudo systemctl enable --now diakopad-vt-toggle.service
EOF

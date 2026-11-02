#!/bin/bash
# Turns the Ubuntu Studio laptop into a "power on -> DiakoPad" appliance:
#
#   * SDDM logs this user in automatically (no password screen).
#   * KDE starts with an empty session, so windows from the last shutdown
#     (e.g. a second Firefox) don't come back next to the DiakoPad one.
#   * diakopad.service is enabled, so the backend starts with the session.
#   * The autostart entry from install-ubuntu-studio.sh then opens
#     http://localhost:8080 in Firefox kiosk mode (diakopad-open-browser.sh).
#
# Run as your normal user after install-ubuntu-studio.sh (sudo is asked for
# the SDDM/GRUB files):
#
#     bash deploy/setup-autoboot.sh            # autologin + session + service
#     bash deploy/setup-autoboot.sh --grub     # also skip the GRUB menu wait
#     bash deploy/setup-autoboot.sh --disable  # back to the password screen
#
# With --grub the menu is hidden with a 0s timeout; hold Shift (BIOS) or
# press Esc (UEFI) during boot to get it back. Don't use it on a dual-boot
# laptop unless you're fine with that.
set -euo pipefail

# Must run as the user who should be logged in automatically: under
# `sudo bash setup-autoboot.sh` $USER is root, which would write
# User=root to the SDDM autologin and fail at `systemctl --user`.
if [ "$(id -u)" -eq 0 ]; then
  echo "!!  Run this WITHOUT sudo, as your normal user (it calls sudo itself):" >&2
  echo "        bash deploy/setup-autoboot.sh" >&2
  exit 1
fi

SDDM_CONF=/etc/sddm.conf.d/50-diakopad-autologin.conf
GRUB_DROPIN=/etc/default/grub.d/50-diakopad-fastboot.cfg

if [ "${1:-}" = "--disable" ]; then
  sudo rm -f "$SDDM_CONF" "$GRUB_DROPIN"
  [ -x /usr/sbin/update-grub ] && sudo update-grub
  echo "==> Autologin removed; the password screen is back on next boot."
  echo "    (diakopad.service and the Firefox autostart are left as they are.)"
  exit 0
fi

# Reuse the session this user last picked at the SDDM screen (Wayland or
# X11); fall back to Plasma's default. Override with DIAKOPAD_SDDM_SESSION.
SESSION="${DIAKOPAD_SDDM_SESSION:-}"
if [ -z "$SESSION" ] && sudo test -f /var/lib/sddm/state.conf; then
  SESSION="$(sudo sed -n 's/^Session=//p' /var/lib/sddm/state.conf | head -1)"
  SESSION="$(basename "$SESSION")"
fi
SESSION="${SESSION:-plasma.desktop}"

echo "==> Enabling SDDM autologin for $USER (session: $SESSION)"
sudo mkdir -p /etc/sddm.conf.d
sudo tee "$SDDM_CONF" >/dev/null <<EOF
# Managed by DiakoPad deploy/setup-autoboot.sh (--disable removes it)
[Autologin]
User=$USER
Session=$SESSION
Relogin=false
EOF

echo "==> KDE: start every login with an empty session"
if command -v kwriteconfig6 >/dev/null 2>&1; then
  KWRITE=kwriteconfig6
else
  KWRITE=kwriteconfig5
fi
"$KWRITE" --file ksmserverrc --group General --key loginMode emptySession

echo "==> Enabling diakopad.service at login"
systemctl --user daemon-reload
systemctl --user enable diakopad.service

if [ ! -f "$HOME/.config/autostart/diakopad-browser.desktop" ]; then
  echo "!!  ~/.config/autostart/diakopad-browser.desktop not found -" \
       "run deploy/install-ubuntu-studio.sh first so Firefox opens on login."
fi

if [ "${1:-}" = "--grub" ]; then
  echo "==> GRUB: hidden menu, 0s timeout"
  sudo mkdir -p /etc/default/grub.d
  sudo tee "$GRUB_DROPIN" >/dev/null <<'EOF'
# Managed by DiakoPad deploy/setup-autoboot.sh (--disable removes it)
GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=0
GRUB_RECORDFAIL_TIMEOUT=0
EOF
  sudo update-grub
fi

cat <<EOF

==> Done. Reboot to test: the laptop should land straight on DiakoPad in
    fullscreen (Alt+F4 leaves the kiosk; DIAKOPAD_KIOSK=0 in the autostart
    Exec= line gives a normal window).

    If a KWallet password prompt shows up after autologin (Wi-Fi passwords
    stored in the wallet), open the Wi-Fi connection in System Settings and
    tick "All users may connect to this network" so it's stored system-wide.
EOF

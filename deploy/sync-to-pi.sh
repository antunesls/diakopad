#!/bin/bash
# Syncs the working tree to the Raspberry Pi for testing, WITHOUT touching
# runtime data. Run from the repo root on a Unix dev machine:
#
#     bash deploy/sync-to-pi.sh [user@host]
#
# Defaults to the alias from ~/.ssh/config (see README). On Windows, use the
# equivalent tar pipeline documented in README.md ("Deploy rápido"), and make
# sure backend/diakopad.db and backend/samples are EXCLUDED - overwriting the
# Pi's database wipes every pad assignment, sample catalog row, effect chain
# and knob mapping (recoverable only from the sfz bank, see
# deploy/recover_pads_from_sfz.py).
set -euo pipefail

TARGET="${1:-diakopad-pi}"
REMOTE_DIR="${DIAKOPAD_REMOTE_DIR:-/home/zynthian/diakopad}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Syncing $REPO_DIR -> $TARGET:$REMOTE_DIR (runtime data preserved)"
rsync -az --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude "diakopad.db" \
  --exclude "samples" \
  --exclude "sfz_bank" \
  --exclude "generated" \
  --exclude ".pytest_cache" \
  "$REPO_DIR"/ "$TARGET:$REMOTE_DIR"/

echo "==> Restarting diakopad.service"
ssh "$TARGET" "sudo systemctl restart diakopad.service && sleep 3 && systemctl is-active diakopad.service"

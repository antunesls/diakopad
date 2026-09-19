#!/bin/bash
# Deploys DiakoPad via git instead of rsync/tar: fetches origin and resets
# the remote checkout to match it exactly, then restarts diakopad.service.
# The target host's checkout must already have `origin` configured (see
# README) and reachable (GitHub over HTTPS or SSH, whichever that host uses).
#
#     bash deploy/git-deploy.sh [user@host] [branch]
#
# Defaults to the "diakopad-laptop" alias (see README's SSH config) and the
# "main" branch. Override with:
#   DIAKOPAD_REMOTE_DIR     checkout path on the target (default: ~/diakopad)
#   DIAKOPAD_SERVICE_SCOPE  "user" (default - systemd --user, laptop/desktop)
#                           or "system" (sudo systemctl, Pi/Zynthian)
#
# This is a real deploy, not a sync: `git reset --hard` DISCARDS any local
# changes on the target (uncommitted edits, stray files under a tracked
# path) so the checkout matches origin/<branch> byte-for-byte. Untracked
# files are left alone. Runtime data (backend/diakopad.db, samples, sfz_bank,
# generated) is already untracked via .gitignore, so a reset never touches it.
set -euo pipefail

TARGET="${1:-diakopad-laptop}"
BRANCH="${2:-main}"
REMOTE_DIR="${DIAKOPAD_REMOTE_DIR:-~/diakopad}"
SERVICE_SCOPE="${DIAKOPAD_SERVICE_SCOPE:-user}"

if [ "$SERVICE_SCOPE" = "system" ]; then
  RESTART_CMD="sudo systemctl restart diakopad.service"
  STATUS_CMD="systemctl is-active diakopad.service"
else
  RESTART_CMD="systemctl --user restart diakopad.service"
  STATUS_CMD="systemctl --user is-active diakopad.service"
fi

echo "==> Fetching $BRANCH on $TARGET:$REMOTE_DIR"
ssh "$TARGET" "cd $REMOTE_DIR && git fetch origin $BRANCH"

echo "==> Local changes on $TARGET that this will discard (if any):"
ssh "$TARGET" "cd $REMOTE_DIR && git status --short" || true

echo "==> Resetting $TARGET:$REMOTE_DIR to origin/$BRANCH"
ssh "$TARGET" "cd $REMOTE_DIR && git reset --hard origin/$BRANCH"

echo "==> Restarting diakopad.service ($SERVICE_SCOPE) on $TARGET"
ssh "$TARGET" "$RESTART_CMD && sleep 2 && $STATUS_CMD"

#!/bin/bash
# Waits for the DiakoPad web app to answer, then opens it in Firefox.
# Runs once per login session via ~/.config/autostart/diakopad-browser.desktop
# (install-ubuntu-studio.sh installs both).
#
#   * Kiosk mode by default (fullscreen, no toolbars). Leave with Alt+F4.
#     Set DIAKOPAD_KIOSK=0 (e.g. in the autostart Exec= line) for a normal
#     window.
#   * Dedicated Firefox profile, so the stage window never inherits tabs,
#     "restore session?" bars or first-run pages from the everyday profile,
#     and doesn't collide with a Firefox the user already has open.
#   * Screen blanking, screen lock and suspend are inhibited only while this
#     window is open (kde-inhibit, falling back to systemd-inhibit).
#   * If Firefox crashes it is relaunched; a clean exit (Alt+F4) ends here.
URL="${DIAKOPAD_URL:-http://localhost:8080/}"
KIOSK="${DIAKOPAD_KIOSK:-1}"

# At boot the backend comes up together with PipeWire and the sfizz/mod-host
# engine, which can take a while - wait up to 2 minutes, then open anyway.
for _ in $(seq 1 120); do
  curl -sf "$URL" -o /dev/null && break
  sleep 1
done

# Snap Firefox (Ubuntu default) can only write under ~/snap/firefox.
if [ -d /snap/firefox ]; then
  PROFILE_DIR="$HOME/snap/firefox/common/diakopad-profile"
else
  PROFILE_DIR="$HOME/.local/share/diakopad/firefox-profile"
fi
mkdir -p "$PROFILE_DIR"
cat > "$PROFILE_DIR/user.js" <<'EOF'
// Managed by diakopad-open-browser.sh - rewritten on every launch.
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.max_resumed_crashes", 0);
user_pref("browser.startup.page", 0);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.translations.automaticallyPopup", false);
user_pref("full-screen-api.warning.timeout", 0);
user_pref("browser.download.panel.shown", true);
EOF

ARGS=(--profile "$PROFILE_DIR" --no-remote)
[ "$KIOSK" = "1" ] && ARGS+=(--kiosk)

if command -v kde-inhibit >/dev/null 2>&1; then
  INHIBIT=(kde-inhibit --power --screenSaver)
else
  INHIBIT=(systemd-inhibit --what=idle:sleep --who=DiakoPad --why="Show em andamento")
fi

for _ in $(seq 1 5); do
  "${INHIBIT[@]}" firefox "${ARGS[@]}" "$URL" && exit 0
  sleep 2
done

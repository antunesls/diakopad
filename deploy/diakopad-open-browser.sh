#!/bin/bash
# Waits for the DiakoPad web app to answer, then opens it in the browser.
# Runs once per login session via ~/.config/autostart/diakopad-browser.desktop
# (install-ubuntu-studio.sh installs both).
for i in $(seq 1 30); do
  curl -sf http://localhost:8080/ -o /dev/null && break
  sleep 1
done
exec firefox --new-window "http://localhost:8080/"

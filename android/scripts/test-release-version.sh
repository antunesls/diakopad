#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

actual="$(bash android/scripts/release-version.sh v0.1.0)"
[[ "$actual" == $'name=0.1.0\ncode=100' ]] || { echo "Unexpected first version: $actual" >&2; exit 1; }
actual="$(bash android/scripts/release-version.sh v2.3.14)"
[[ "$actual" == $'name=2.3.14\ncode=20314' ]] || { echo "Unexpected upgrade version: $actual" >&2; exit 1; }
for invalid in v0.0.0 v1.100.0 v1.2.100 latest v1.2.3-beta; do
    if bash android/scripts/release-version.sh "$invalid" >/dev/null 2>&1; then
        echo "Accepted invalid tag: $invalid" >&2
        exit 1
    fi
done

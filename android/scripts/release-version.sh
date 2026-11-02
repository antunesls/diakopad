#!/usr/bin/env bash
set -euo pipefail

tag="${1:?usage: release-version.sh vMAJOR.MINOR.PATCH}"
if [[ ! "$tag" =~ ^v(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]?)\.(0|[1-9][0-9]?)$ ]]; then
    echo "Tag must be vMAJOR.MINOR.PATCH (minor/patch < 100)" >&2
    exit 1
fi
major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
code=$((major * 10000 + minor * 100 + patch))
if (( code < 1 )); then
    echo "Android versionCode must be positive" >&2
    exit 1
fi
printf 'name=%s\ncode=%s\n' "${tag#v}" "$code"

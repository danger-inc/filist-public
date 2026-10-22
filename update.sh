#!/usr/bin/env bash
# Neueste filist-Version aus dem Git-Repo holen und installieren.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
old="$(cat app/VERSION 2>/dev/null || echo '?')"
git pull --ff-only
./install.sh --update
echo "filist $old → $(cat app/VERSION)"

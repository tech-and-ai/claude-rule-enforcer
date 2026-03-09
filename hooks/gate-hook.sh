#!/usr/bin/env bash
# CRE Gate Hook — called by Claude Code plugin system
# Reads tool input from stdin, outputs decision JSON to stdout

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Try the venv binary first (editable install), then system cre, then python module
if [ -x "$PLUGIN_ROOT/.venv/bin/cre" ]; then
    exec "$PLUGIN_ROOT/.venv/bin/cre" gate
elif command -v cre &>/dev/null; then
    exec cre gate
else
    exec python3 -m cre.cli gate
fi

#!/usr/bin/env bash
# ===================================================================
# LUKS-COMPANION: Discord Webhook Notification Wrapper
# Dispatches call to standalone module scripts/notify-discord.py
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/notify-discord.py"

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$PYTHON_SCRIPT" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$PYTHON_SCRIPT" "$@"
else
    echo "[!] Python 3 non trovato nel sistema. Notifica Discord ignorata." >&2
    exit 0
fi

#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER.SH - Retrocompatibility Wrapper for luks-companion
# Automatically delegates all CLI operations to python3 -m luks_companion
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

if command -v python3 >/dev/null 2>&1; then
    exec python3 -m luks_companion "$@"
else
    echo "[!] ERRORE: python3 non è installato nel sistema." >&2
    exit 1
fi

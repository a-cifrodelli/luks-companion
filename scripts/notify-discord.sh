#!/usr/bin/env bash
# ===================================================================
# LUKS-COMPANION: Discord Webhook Notification Dispatcher
# Rich Discord embeds on unlock, lock, watchdog timeout, or errors.
# If DISCORD_WEBHOOK_URL is missing or empty, exits 0 immediately.
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"

# Security & Bypass check: If webhook URL is empty or invalid, exit immediately with zero overhead
if [ -z "$WEBHOOK_URL" ] || [[ "$WEBHOOK_URL" != https://* ]]; then
    exit 0
fi

EVENT="${1:-test}"
CUSTOM_MSG="${2:-}"
HOST_NAME="$(hostname 2>/dev/null || echo "Linux Server")"
NOW_ISO="$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")"

COLOR=3718648 # Default Cyan (0x38BDF8)
TITLE="ℹ️ Notifica LUKS Companion"
DESC="Evento storage registrato sul server."

case "$EVENT" in
    unlock|start)
        COLOR=1096065 # Green (0x10B981)
        TITLE="🔓 Storage LUKS Sbloccato & Montato"
        DESC="Il container cifrato LUKS2 è stato sbloccato in RAM ed i volumi sono montati e pronti all'uso."
        ;;
    lock|stop)
        COLOR=15680580 # Red (0xEF4444)
        TITLE="🛑 Storage LUKS Sigillato & Spento"
        DESC="Filesystem smontati, chiave distrutta da RAM, testine SCSI parcheggiate ed alimentazione 220V interrotta (0W Standby)."
        ;;
    watchdog)
        COLOR=16096779 # Amber (0xF59E0B)
        TITLE="⏱️ Watchdog: Arresto Automatico per Inattività"
        DESC="Nessuna attività I/O rilevata sul disco per ${IDLE_TIMEOUT_MIN:-30} minuti. Lo storage è stato espulso e spento in sicurezza (0W Standby)."
        ;;
    error|fail)
        COLOR=15680580 # Red (0xEF4444)
        TITLE="⚠️ Errore Storage LUKS"
        DESC="${CUSTOM_MSG:-Si è verificato un errore durante un'operazione sul sottosistema storage.}"
        ;;
    test)
        COLOR=3718648 # Cyan (0x38BDF8)
        TITLE="🧪 Test Notifiche LUKS Companion"
        DESC="Integrazione webhook Discord verificata e funzionante con successo!"
        ;;
    *)
        TITLE="ℹ️ LUKS Companion: ${EVENT}"
        DESC="${CUSTOM_MSG:-Notifica dal sottosistema storage.}"
        ;;
esac

# Build dynamic fields
VG_NAME_VAL="${VG_NAME:-N/D}"
MOUNT_VAL="${MOUNT_CRYPTO:-N/D}"
WEBDAV_VAL="Inattivo"
if [ "${ENABLE_WEBDAV:-true}" = "true" ]; then
    WEBDAV_VAL="Attivo (Porta ${WEBDAV_PORT:-9088})"
fi

# Construct JSON payload using python3 if available (safest escaping) or fallback
if command -v python3 >/dev/null 2>&1; then
    PAYLOAD=$(python3 -c '
import json, sys, os

event = sys.argv[1]
title = sys.argv[2]
desc = sys.argv[3]
color = int(sys.argv[4])
host = sys.argv[5]
vg = sys.argv[6]
mount = sys.argv[7]
webdav = sys.argv[8]
timestamp = sys.argv[9]
custom = sys.argv[10]

fields = [
    {"name": "🖥️ Host", "value": f"`{host}`", "inline": True},
    {"name": "📦 Volume Group", "value": f"`{vg}`", "inline": True},
]

if event in ["unlock", "start"]:
    fields.append({"name": "📁 Punto di Mount", "value": f"`{mount}`", "inline": False})
    fields.append({"name": "🌐 WebDAV", "value": webdav, "inline": True})
elif event in ["lock", "stop", "watchdog"]:
    fields.append({"name": "⚡ Alimentazione", "value": "0W Standby (Spento)", "inline": True})

if custom and event not in ["error", "fail"]:
    fields.append({"name": "📝 Dettagli", "value": custom, "inline": False})

payload = {
    "username": "LUKS Companion",
    "avatar_url": "https://raw.githubusercontent.com/a-cifrodelli/luks-companion/main/web/static/favicon.svg",
    "embeds": [
        {
            "title": title,
            "description": desc,
            "color": color,
            "fields": fields,
            "footer": {
                "text": "LUKS Companion • Zero-Standby Storage Subsystem"
            },
            "timestamp": timestamp
        }
    ]
}

print(json.dumps(payload))
' "$EVENT" "$TITLE" "$DESC" "$COLOR" "$HOST_NAME" "$VG_NAME_VAL" "$MOUNT_VAL" "$WEBDAV_VAL" "$NOW_ISO" "$CUSTOM_MSG")
else
    # Simple fallback payload
    PAYLOAD=$(cat <<EOF
{
  "username": "LUKS Companion",
  "embeds": [
    {
      "title": "${TITLE}",
      "description": "${DESC}",
      "color": ${COLOR},
      "footer": { "text": "LUKS Companion • Zero-Standby Storage Subsystem" },
      "timestamp": "${NOW_ISO}"
    }
  ]
}
EOF
)
fi

# Send webhook asynchronously with 5s timeout to never block main script
(
    curl -s -f --max-time 5 -X POST \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" \
        "$WEBHOOK_URL" >/dev/null 2>&1 || true
) &

exit 0

#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated UNIX Socket Daemon Installer
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/config/luks-managerd.service.template"
TARGET_SERVICE_FILE="/etc/systemd/system/luks-managerd.service"

if [ "$EUID" -ne 0 ]; then
    echo "[!] ERRORE: Questo script deve essere eseguito come root (sudo ./scripts/install-daemon.sh)" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo "[!] ERRORE: Template di servizio demone non trovato in ${SERVICE_TEMPLATE_FILE}" >&2
    exit 1
fi

echo "=== LUKS MANAGER: INSTALLAZIONE DEMONE SOCKET API ==="

# Make Python script executable
chmod +x "${SCRIPT_DIR}/scripts/luks-managerd.py"

echo "[1/3] Generazione /etc/systemd/system/luks-managerd.service..."
export SCRIPT_DIR
envsubst '$SCRIPT_DIR' < "$SERVICE_TEMPLATE_FILE" > "$TARGET_SERVICE_FILE"

chmod 644 "$TARGET_SERVICE_FILE"

echo "[2/3] Ricarica demone systemd e abilitazione avvio automatico..."
systemctl daemon-reload
systemctl enable luks-managerd.service

echo "[3/3] Avvio del servizio luks-managerd..."
systemctl restart luks-managerd.service

echo -e "\n=== DEMONE SOCKET REGISTRATO ED AVVIATO CON SUCCESSO! ==="
echo "Il demone è in ascolto su socket UNIX: /run/luks-manager.sock"
echo "Qualsiasi WebApp/interfaccia locale può ora comunicare inviando JSON:"
echo "  - Stato:    echo '{\"action\": \"status\"}' | nc -U /run/luks-manager.sock"
echo "  - Sblocco:  echo '{\"action\": \"unlock\", \"passphrase\": \"tua_passphrase\"}' | nc -U /run/luks-manager.sock"
echo "  - Arresto:  echo '{\"action\": \"stop\"}' | nc -U /run/luks-manager.sock"

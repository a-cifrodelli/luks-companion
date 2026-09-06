#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated Systemd Service Installer
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/config/luks-manager.service.template"
TARGET_SERVICE_FILE="/etc/systemd/system/luks-manager.service"

if [ "$EUID" -ne 0 ]; then
    echo "[!] ERRORE: Questo script deve essere eseguito come root (sudo ./scripts/install-service.sh)" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo "[!] ERRORE: Template di servizio non trovato in ${SERVICE_TEMPLATE_FILE}" >&2
    exit 1
fi

echo "=== LUKS MANAGER: REGISTRAZIONE SERVIZIO SYSTEMD ==="

echo "[1/2] Generazione /etc/systemd/system/luks-manager.service (Directory: ${SCRIPT_DIR})..."
export SCRIPT_DIR
envsubst '$SCRIPT_DIR' < "$SERVICE_TEMPLATE_FILE" > "$TARGET_SERVICE_FILE"

chmod 644 "$TARGET_SERVICE_FILE"

echo "[2/2] Ricarica demone systemd..."
systemctl daemon-reload

echo -e "\n=== SERVIZIO REGISTRATO CON SUCCESSO! ==="
echo "Ora puoi gestire luks-manager tramite systemd:"
echo "  - Avvio:    sudo systemctl start luks-manager"
echo "  - Stato:    sudo systemctl status luks-manager"
echo "  - Arresto:  sudo systemctl stop luks-manager"

#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated Web Dashboard Gateway Installer
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/luks-web.service.template"
TARGET_SERVICE_FILE="/etc/systemd/system/luks-web.service"

if [ "$EUID" -ne 0 ]; then
    echo "[!] ERRORE: Questo script deve essere eseguito come root (sudo ./web/install.sh)" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo "[!] ERRORE: Template di servizio web non trovato in ${SERVICE_TEMPLATE_FILE}" >&2
    exit 1
fi

echo "=== LUKS MANAGER: INSTALLAZIONE WEB APP DASHBOARD ==="

# Set executable permissions
chmod +x "${SCRIPT_DIR}/server.py"

echo "[1/3] Generazione /etc/systemd/system/luks-web.service..."
export REPO_DIR
envsubst '$REPO_DIR' < "$SERVICE_TEMPLATE_FILE" > "$TARGET_SERVICE_FILE"

chmod 644 "$TARGET_SERVICE_FILE"

echo "[2/3] Ricarica demone systemd e abilitazione avvio automatico..."
systemctl daemon-reload
systemctl enable luks-web.service

echo "[3/3] Avvio del servizio luks-web..."
systemctl restart luks-web.service

echo -e "\n=== WEB APP REGISTRATA ED AVVIATA CON SUCCESSO! ==="
echo "La dashboard è accessibile su:"
echo "  -> http://<IP_RASPBERRY_PI>:9099"
echo "  -> o tramite reverse proxy Traefik (es. https://nas.rpi.lan)"

#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated Web Dashboard Gateway Installer
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/luks-web.service.template"
TARGET_SERVICE_FILE="/etc/systemd/system/luks-web.service"
ENV_FILE="${REPO_DIR}/.env"

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

# Read configured port from .env if present
WEB_PORT="9099"
if [ -f "$ENV_FILE" ]; then
    WEB_PORT_CONFIG=$(grep -E "^WEB_PORT=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'" || echo "")
    if [ -n "$WEB_PORT_CONFIG" ]; then
        WEB_PORT="$WEB_PORT_CONFIG"
    fi
fi

echo -e "\n=== WEB APP REGISTRATA ED AVVIATA CON SUCCESSO! ==="
echo "La dashboard HTTP è ora in ascolto sulla porta: ${WEB_PORT}"
echo "Accesso diretto:"
echo "  -> http://<IP_DEL_SERVER>:${WEB_PORT}"
echo ""
echo "Nota TLS / HTTPS: Per esporre l'interfaccia con certificato SSL/HTTPS,"
echo "configura il tuo reverse proxy preferito (Nginx, Traefik, Caddy, Apache) che"
echo "inoltra le richieste verso http://127.0.0.1:${WEB_PORT}."

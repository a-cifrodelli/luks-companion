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

# ANSI Color Palette
CLR_RESET="\033[0m"
CLR_BOLD="\033[1m"
CLR_CYAN="\033[0;36m"
CLR_BCYAN="\033[1;36m"
CLR_GREEN="\033[1;32m"
CLR_YELLOW="\033[1;33m"
CLR_RED="\033[1;31m"
CLR_WHITE="\033[1;37m"

if [ "$EUID" -ne 0 ]; then
    echo -e "${CLR_RED}[✗] ERRORE: Questo script deve essere eseguito come root (sudo ./web/install.sh)${CLR_RESET}" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo -e "${CLR_RED}[✗] ERRORE: Template di servizio web non trovato in ${SERVICE_TEMPLATE_FILE}${CLR_RESET}" >&2
    exit 1
fi

echo -e "${CLR_BCYAN}=== LUKS MANAGER: INSTALLAZIONE WEB APP DASHBOARD ===${CLR_RESET}"

# Ensure luks-web group exists for socket IPC isolation
if ! getent group luks-web >/dev/null 2>&1; then
    echo -e "  [*] Creazione gruppo di sistema 'luks-web'..."
    groupadd -r luks-web || true
fi

# Detect owner of repository directory to prevent 200/CHDIR Permission Denied on /home/...
REPO_OWNER=$(stat -c '%U' "$REPO_DIR" 2>/dev/null || echo "${SUDO_USER:-root}")
if [ "$REPO_OWNER" = "root" ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    REPO_OWNER="$SUDO_USER"
fi

if [ "$REPO_OWNER" != "root" ]; then
    SERVICE_USER="$REPO_OWNER"
    echo -e "  [*] Configurazione servizio per l'utente non privilegiato proprietario della directory: ${CLR_WHITE}${SERVICE_USER}${CLR_RESET}"
    usermod -aG luks-web "$SERVICE_USER" || true
else
    SERVICE_USER="root"
fi

# Set executable permissions
chmod +x "${SCRIPT_DIR}/server.py"

echo -e "\n${CLR_CYAN}[1/3] Generazione ${CLR_WHITE}${TARGET_SERVICE_FILE}${CLR_CYAN}...${CLR_RESET}"
export REPO_DIR
export SERVICE_USER
envsubst '$REPO_DIR $SERVICE_USER' < "$SERVICE_TEMPLATE_FILE" > "$TARGET_SERVICE_FILE"

chmod 644 "$TARGET_SERVICE_FILE"
echo -e "  ${CLR_GREEN}[✓] File di servizio systemd generato con successo.${CLR_RESET}"

echo -e "\n${CLR_CYAN}[2/3] Ricarica demone systemd e abilitazione avvio automatico...${CLR_RESET}"
systemctl daemon-reload
systemctl enable luks-web.service
echo -e "  ${CLR_GREEN}[✓] Servizio abilitato all'avvio.${CLR_RESET}"

echo -e "\n${CLR_CYAN}[3/3] Avvio del servizio luks-web...${CLR_RESET}"
systemctl restart luks-web.service
echo -e "  ${CLR_GREEN}[✓] Servizio luks-web avviato.${CLR_RESET}"

# Read configured port from .env if present
WEB_PORT="9099"
if [ -f "$ENV_FILE" ]; then
    WEB_PORT_CONFIG=$(grep -E "^WEB_PORT=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'" || echo "")
    if [ -n "$WEB_PORT_CONFIG" ]; then
        WEB_PORT="$WEB_PORT_CONFIG"
    fi
fi

echo -e "\n${CLR_GREEN}=== WEB APP REGISTRATA ED AVVIATA CON SUCCESSO! ===${CLR_RESET}"
echo -e "La dashboard HTTP è ora in ascolto sulla porta: ${CLR_WHITE}${WEB_PORT}${CLR_RESET}"
echo -e "Accesso diretto:"
echo -e "  -> ${CLR_BOLD}http://<IP_DEL_SERVER>:${WEB_PORT}${CLR_RESET}"
echo ""
echo -e "${CLR_YELLOW}Nota Terminazione TLS / HTTPS:${CLR_RESET}"
echo "Per esporre l'interfaccia con certificato SSL/HTTPS, configura il tuo reverse"
echo "proxy preferito (Traefik, Nginx, Caddy, Apache) inoltrando le richieste verso http://127.0.0.1:${WEB_PORT}."

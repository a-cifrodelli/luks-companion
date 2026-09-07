#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated UNIX Socket Daemon Installer
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/luks-managerd.service.template"
TARGET_SERVICE_FILE="/etc/systemd/system/luks-managerd.service"

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
    echo -e "${CLR_RED}[✗] ERRORE: Questo script deve essere eseguito come root (sudo ./daemon/install.sh)${CLR_RESET}" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo -e "${CLR_RED}[✗] ERRORE: Template di servizio demone non trovato in ${SERVICE_TEMPLATE_FILE}${CLR_RESET}" >&2
    exit 1
fi

echo -e "${CLR_BCYAN}=== LUKS MANAGER: INSTALLAZIONE DEMONE SOCKET API ===${CLR_RESET}"

# Ensure luks-web group exists for socket IPC isolation
if ! getent group luks-web >/dev/null 2>&1; then
    echo -e "  [*] Creazione gruppo di sistema 'luks-web' per isolamento socket..."
    groupadd -r luks-web || true
fi

# Set executable permissions
chmod +x "${SCRIPT_DIR}/luks-managerd.py"
chmod +x "${REPO_DIR}/luks-manager.sh"

echo -e "\n${CLR_CYAN}[1/3] Generazione ${CLR_WHITE}${TARGET_SERVICE_FILE}${CLR_CYAN}...${CLR_RESET}"
export REPO_DIR
envsubst '$REPO_DIR' < "$SERVICE_TEMPLATE_FILE" > "$TARGET_SERVICE_FILE"

chmod 644 "$TARGET_SERVICE_FILE"
echo -e "  ${CLR_GREEN}[✓] File di servizio systemd generato con successo.${CLR_RESET}"

echo -e "\n${CLR_CYAN}[2/3] Ricarica demone systemd e abilitazione avvio automatico...${CLR_RESET}"
systemctl daemon-reload
systemctl enable luks-managerd.service
echo -e "  ${CLR_GREEN}[✓] Servizio abilitato all'avvio.${CLR_RESET}"

echo -e "\n${CLR_CYAN}[3/3] Avvio del servizio luks-managerd...${CLR_RESET}"
systemctl restart luks-managerd.service
echo -e "  ${CLR_GREEN}[✓] Servizio luks-managerd avviato.${CLR_RESET}"

echo -e "\n${CLR_GREEN}=== DEMONE SOCKET REGISTRATO ED AVVIATO CON SUCCESSO! ===${CLR_RESET}"
echo -e "Il demone è in ascolto su socket UNIX: ${CLR_WHITE}/run/luks-manager.sock${CLR_RESET}"
echo -e "Permessi socket impostati su ${CLR_WHITE}0666${CLR_RESET} (accessibile da WebApp non-root)."
echo ""
echo -e "${CLR_BOLD}Comandi di test rapido via CLI:${CLR_RESET}"
echo -e "  - Stato (socat):   ${CLR_CYAN}echo '{\"action\": \"status\"}' | socat -t 30 - UNIX-CONNECT:/run/luks-manager.sock${CLR_RESET}"
echo -e "  - Sblocco (socat): ${CLR_CYAN}echo '{\"action\": \"unlock\", \"passphrase\": \"tua_passphrase\"}' | socat -t 60 - UNIX-CONNECT:/run/luks-manager.sock${CLR_RESET}"
echo -e "  - Arresto (socat): ${CLR_CYAN}echo '{\"action\": \"stop\"}' | socat -t 30 - UNIX-CONNECT:/run/luks-manager.sock${CLR_RESET}"

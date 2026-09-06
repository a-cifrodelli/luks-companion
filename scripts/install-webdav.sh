#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated WebDAV Server Installer (ARM64 / RPi5)
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
YAML_TEMPLATE_FILE="${SCRIPT_DIR}/config/webdav.yaml.template"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/config/webdav.service.template"

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
    echo -e "${CLR_RED}[✗] ERRORE: Questo script deve essere eseguito come root (sudo ./scripts/install-webdav.sh)${CLR_RESET}" >&2
    exit 1
fi

# Load .env file (mandatory)
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
else
    echo -e "${CLR_RED}[✗] ERRORE: File di configurazione .env non trovato in ${ENV_FILE}${CLR_RESET}" >&2
    echo -e "${CLR_YELLOW}    Copia .env.example in .env e definisci i tuoi parametri prima di installare.${CLR_RESET}" >&2
    exit 1
fi

if [ -z "${MOUNT_CRYPTO:-}" ]; then
    echo -e "${CLR_RED}[✗] ERRORE: La variabile MOUNT_CRYPTO non è definita nel file .env!${CLR_RESET}" >&2
    exit 1
fi

WEBDAV_PORT="${WEBDAV_PORT:-9088}"
STORAGE_GROUP="${STORAGE_GROUP:-storage}"
STORAGE_PERMS="${STORAGE_PERMS:-2775}"

# Detect real invoking non-root user (no hardcoding)
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "$USER")}"

if [ ! -f "$YAML_TEMPLATE_FILE" ]; then
    echo -e "${CLR_RED}[✗] ERRORE: Template YAML non trovato in ${YAML_TEMPLATE_FILE}${CLR_RESET}" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo -e "${CLR_RED}[✗] ERRORE: Template Service non trovato in ${SERVICE_TEMPLATE_FILE}${CLR_RESET}" >&2
    exit 1
fi

echo -e "${CLR_BCYAN}=== LUKS MANAGER: INSTALLAZIONE SERVER WEBDAV ===${CLR_RESET}"

# 1. GROUP & PERMISSION SETUP (NO 777, SHARED STORAGE GROUP)
echo -e "\n${CLR_CYAN}[1/5] Configurazione gruppo permessi di sistema '${CLR_WHITE}${STORAGE_GROUP}${CLR_CYAN}'...${CLR_RESET}"
if ! getent group "$STORAGE_GROUP" >/dev/null 2>&1; then
    groupadd -r "$STORAGE_GROUP" 2>/dev/null || groupadd "$STORAGE_GROUP"
    echo -e "  ${CLR_GREEN}[✓] Gruppo di sistema '${STORAGE_GROUP}' creato.${CLR_RESET}"
else
    echo -e "  ${CLR_CYAN}[*] Gruppo '${STORAGE_GROUP}' già esistente.${CLR_RESET}"
fi

if [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
    usermod -aG "$STORAGE_GROUP" "$REAL_USER" 2>/dev/null || true
    echo -e "  ${CLR_GREEN}[✓] Utente reale '${REAL_USER}' aggiunto al gruppo '${STORAGE_GROUP}'.${CLR_RESET}"
fi

mkdir -p "$MOUNT_CRYPTO"
chgrp "$STORAGE_GROUP" "$MOUNT_CRYPTO" 2>/dev/null || true
chmod "$STORAGE_PERMS" "$MOUNT_CRYPTO" 2>/dev/null || true

# 2. DOWNLOAD & INSTALL BINARY
echo -e "\n${CLR_CYAN}[2/5] Download del binario standalone WebDAV per Linux ARM64...${CLR_RESET}"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

curl -fsSL "https://github.com/hacdias/webdav/releases/latest/download/linux-arm64-webdav.tar.gz" -o "${TMP_DIR}/webdav.tar.gz"
tar -xzf "${TMP_DIR}/webdav.tar.gz" -C "$TMP_DIR"

mv "${TMP_DIR}/webdav" /usr/local/bin/webdav
chmod +x /usr/local/bin/webdav
echo -e "  ${CLR_GREEN}[✓] Binario installato con successo in /usr/local/bin/webdav${CLR_RESET}"

# 3. PROMPT FOR WEBDAV CREDENTIALS
echo -e "\n${CLR_CYAN}[3/5] Configurazione credenziali WebDAV (Porta ${CLR_WHITE}${WEBDAV_PORT}${CLR_CYAN})...${CLR_RESET}"
read -p "Inserisci nome utente WebDAV [admin]: " WEBDAV_USER
WEBDAV_USER="${WEBDAV_USER:-admin}"

read -rs -p "Inserisci password per utente '$WEBDAV_USER': " WEBDAV_PASS
echo ""

# Generate Bcrypt hash using python
RAW_HASH=$(python3 -c "
import sys
try:
    import bcrypt
    print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())
except ImportError:
    import subprocess
    print(subprocess.check_output(['python3', '-c', 'import hashlib; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())'], text=True).strip())
" "$WEBDAV_PASS" 2>/dev/null || echo "$WEBDAV_PASS")

if [[ "$RAW_HASH" == \$2* ]]; then
    WEBDAV_PASSWORD_HASH="{bcrypt}${RAW_HASH}"
else
    WEBDAV_PASSWORD_HASH="${RAW_HASH}"
fi

# 4. POPULATE CONFIG FILE FROM TEMPLATE
echo -e "\n${CLR_CYAN}[4/5] Generazione /etc/webdav/config.yaml (Mount: ${CLR_WHITE}${MOUNT_CRYPTO}${CLR_CYAN})...${CLR_RESET}"
mkdir -p /etc/webdav

export WEBDAV_USER WEBDAV_PASSWORD_HASH MOUNT_CRYPTO WEBDAV_PORT
envsubst '$WEBDAV_USER $WEBDAV_PASSWORD_HASH $MOUNT_CRYPTO $WEBDAV_PORT' < "$YAML_TEMPLATE_FILE" > /etc/webdav/config.yaml

chgrp "$STORAGE_GROUP" /etc/webdav/config.yaml 2>/dev/null || true
chmod 640 /etc/webdav/config.yaml
echo -e "  ${CLR_GREEN}[✓] Configurazione applicata in /etc/webdav/config.yaml${CLR_RESET}"

# 5. INSTANTIATE SYSTEMD SERVICE FROM TEMPLATE
echo -e "\n${CLR_CYAN}[5/5] Registrazione servizio systemd (webdav.service)...${CLR_RESET}"
cp "$SERVICE_TEMPLATE_FILE" /etc/systemd/system/webdav.service

systemctl daemon-reload
echo -e "  ${CLR_GREEN}[✓] Servizio systemd registrato con successo!${CLR_RESET}"

echo -e "\n${CLR_GREEN}=== INSTALLAZIONE COMPLETATA CON SUCCESSO! ===${CLR_RESET}"
echo -e "Il server WebDAV è pronto sulla porta ${CLR_WHITE}${WEBDAV_PORT}${CLR_RESET} collegato direttamente al punto di mount '${CLR_WHITE}${MOUNT_CRYPTO}${CLR_RESET}'."
echo -e "I permessi di scrittura sono protetti tramite il gruppo '${CLR_WHITE}${STORAGE_GROUP}${CLR_RESET}' (SGID ${STORAGE_PERMS})."
echo -e "Verrà avviato automaticamente da ${CLR_BOLD}luks-manager.sh${CLR_RESET} quando il disco viene montato."

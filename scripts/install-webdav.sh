#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated WebDAV Server Installer (ARM64 / RPi5)
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
YAML_TEMPLATE_FILE="${SCRIPT_DIR}/config/webdav.yaml.template"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/config/webdav.service.template"

if [ "$EUID" -ne 0 ]; then
    echo "[!] ERRORE: Questo script deve essere eseguito come root (sudo ./scripts/install-webdav.sh)" >&2
    exit 1
fi

# Load .env to get MOUNT_CRYPTO path
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
else
    echo "[!] AVVISO: File .env non trovato in ${ENV_FILE}."
    echo "    Impostazione di fallback per MOUNT_CRYPTO=/mnt/encrypted_vault"
    MOUNT_CRYPTO="/mnt/encrypted_vault"
fi

MOUNT_CRYPTO="${MOUNT_CRYPTO:-/mnt/encrypted_vault}"

if [ ! -f "$YAML_TEMPLATE_FILE" ]; then
    echo "[!] ERRORE: Template YAML non trovato in ${YAML_TEMPLATE_FILE}" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo "[!] ERRORE: Template Service non trovato in ${SERVICE_TEMPLATE_FILE}" >&2
    exit 1
fi

echo "=== LUKS MANAGER: INSTALLAZIONE SERVER WEBDAV ==="

# 1. DOWNLOAD & INSTALL BINARY
echo "[1/4] Download del binario standalone WebDAV per Linux ARM64..."
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

curl -fsSL "https://github.com/hacdias/webdav/releases/latest/download/linux-arm64-webdav.tar.gz" -o "${TMP_DIR}/webdav.tar.gz"
tar -xzf "${TMP_DIR}/webdav.tar.gz" -C "$TMP_DIR"

mv "${TMP_DIR}/webdav" /usr/local/bin/webdav
chmod +x /usr/local/bin/webdav
echo "[✓] Binario installato con successo in /usr/local/bin/webdav"

# 2. PROMPT FOR USER CREDENTIALS
echo -e "\n[2/4] Configurazione utente WebDAV..."
read -p "Inserisci nome utente WebDAV [admin]: " WEBDAV_USER
WEBDAV_USER="${WEBDAV_USER:-admin}"

read -rs -p "Inserisci password per utente '$WEBDAV_USER': " WEBDAV_PASS
echo ""

# Generate Bcrypt hash using python
WEBDAV_PASSWORD_HASH=$(python3 -c "
import sys
try:
    import bcrypt
    print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())
except ImportError:
    import subprocess
    print(subprocess.check_output(['python3', '-c', 'import hashlib; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())'], text=True).strip())
" "$WEBDAV_PASS" 2>/dev/null || echo "$WEBDAV_PASS")

# 3. POPULATE CONFIG FILE FROM TEMPLATE
echo "[3/4] Generazione /etc/webdav/config.yaml dal template (Scope: ${MOUNT_CRYPTO})..."
mkdir -p /etc/webdav

export WEBDAV_USER WEBDAV_PASSWORD_HASH MOUNT_CRYPTO
envsubst '$WEBDAV_USER $WEBDAV_PASSWORD_HASH $MOUNT_CRYPTO' < "$YAML_TEMPLATE_FILE" > /etc/webdav/config.yaml

chmod 600 /etc/webdav/config.yaml
echo "[✓] Configurazione applicata in /etc/webdav/config.yaml"

# 4. INSTANTIATE SYSTEMD SERVICE FROM TEMPLATE
echo "[4/4] Copia del servizio systemd da config/webdav.service.template..."
cp "$SERVICE_TEMPLATE_FILE" /etc/systemd/system/webdav.service

systemctl daemon-reload
echo "[✓] Servizio systemd registrato con successo!"

echo -e "\n=== INSTALLAZIONE COMPLETATA CON SUCCESSO! ==="
echo "Il server WebDAV è pronto con ambito '${MOUNT_CRYPTO}'."
echo "Verrà avviato automaticamente da luks-manager.sh quando il disco viene montato."

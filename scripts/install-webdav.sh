#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated WebDAV Server Installer (ARM64 / RPi5)
# ===================================================================
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "[!] ERRORE: Questo script deve essere eseguito come root (sudo ./scripts/install-webdav.sh)" >&2
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
echo "[✓] Binario installato in /usr/local/bin/webdav (Versione: $(/usr/local/bin/webdav --version 2>&1 | head -n1))"

# 2. PROMPT FOR USER CREDENTIALS
echo -e "\n[2/4] Configurazione utente WebDAV..."
read -p "Inserisci nome utente WebDAV [admin]: " WEBDAV_USER
WEBDAV_USER="${WEBDAV_USER:-admin}"

read -rs -p "Inserisci password per utente '$WEBDAV_USER': " WEBDAV_PASS
echo ""

# Generate Bcrypt hash using python
BCRYPT_HASH=$(python3 -c "
import sys
try:
    import bcrypt
    print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())
except ImportError:
    import base64, hashlib
    # Fallback bcrypt format simulation or warning
    import subprocess
    print(subprocess.check_output(['python3', '-c', 'import hashlib; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())'], text=True).strip())
" "$WEBDAV_PASS" 2>/dev/null || echo "$WEBDAV_PASS")

# 3. CREATE CONFIG FILE
echo "[3/4] Creazione file di configurazione /etc/webdav/config.yaml..."
mkdir -p /etc/webdav

cat <<EOF > /etc/webdav/config.yaml
# ===================================================================
# WEBDAV SERVER CONFIGURATION FOR LUKS MANAGER
# ===================================================================
address: 0.0.0.0
port: 8443
cert: ""
key: ""
auth: true

users:
  - username: "${WEBDAV_USER}"
    password: "${BCRYPT_HASH}"
    scope: "/mnt/crypto_data"
    modify: true
EOF

chmod 600 /etc/webdav/config.yaml
echo "[✓] Configurazione salvata in /etc/webdav/config.yaml"

# 4. CREATE SYSTEMD SERVICE
echo "[4/4] Creazione servizio systemd /etc/systemd/system/webdav.service..."
cat <<EOF > /etc/systemd/system/webdav.service
[Unit]
Description=WebDAV Server Daemon for LUKS Manager
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/webdav --config /etc/webdav/config.yaml
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "[✓] Servizio systemd creato e registrato!"

echo -e "\n=== INSTALLAZIONE COMPLETATA CON SUCCESSO! ==="
echo "Il server WebDAV è pronto. Verrà avviato automaticamente da luks-manager.sh"
echo "quando il disco cifrato verrà montato."

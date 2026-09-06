#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated UNIX Socket Daemon Installer
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_TEMPLATE_FILE="${SCRIPT_DIR}/luks-managerd.service.template"
TARGET_SERVICE_FILE="/etc/systemd/system/luks-managerd.service"

if [ "$EUID" -ne 0 ]; then
    echo "[!] ERRORE: Questo script deve essere eseguito come root (sudo ./daemon/install.sh)" >&2
    exit 1
fi

if [ ! -f "$SERVICE_TEMPLATE_FILE" ]; then
    echo "[!] ERRORE: Template di servizio demone non trovato in ${SERVICE_TEMPLATE_FILE}" >&2
    exit 1
fi

echo "=== LUKS MANAGER: INSTALLAZIONE DEMONE SOCKET API ==="

# Set executable permissions
chmod +x "${SCRIPT_DIR}/luks-managerd.py"
chmod +x "${REPO_DIR}/luks-manager.sh"

echo "[1/3] Generazione /etc/systemd/system/luks-managerd.service..."
export REPO_DIR
envsubst '$REPO_DIR' < "$SERVICE_TEMPLATE_FILE" > "$TARGET_SERVICE_FILE"

chmod 644 "$TARGET_SERVICE_FILE"

echo "[2/3] Ricarica demone systemd e abilitazione avvio automatico..."
systemctl daemon-reload
systemctl enable luks-managerd.service

echo "[3/3] Avvio del servizio luks-managerd..."
systemctl restart luks-managerd.service

echo -e "\n=== DEMONE SOCKET REGISTRATO ED AVVIATO CON SUCCESSO! ==="
echo "Il demone è in ascolto su socket UNIX: /run/luks-manager.sock"
echo "Permessi socket impostati su 0666 (accessibile da WebApp non-root)."
echo ""
echo "Test di comunicazione via CLI (socat o Python):"
echo "  - Stato (socat):    echo '{\"action\": \"status\"}' | socat -t 30 - UNIX-CONNECT:/run/luks-manager.sock"
echo "  - Sblocco (socat):  echo '{\"action\": \"unlock\", \"passphrase\": \"tua_passphrase\"}' | socat -t 60 - UNIX-CONNECT:/run/luks-manager.sock"
echo "  - Arresto (socat):  echo '{\"action\": \"stop\"}' | socat -t 30 - UNIX-CONNECT:/run/luks-manager.sock"
echo "  - Python 3:         python3 -c 'import socket; s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(\"/run/luks-manager.sock\"); s.sendall(b\"{\\\"action\\\": \\\"status\\\"}\"); print(s.recv(4096).decode())'"

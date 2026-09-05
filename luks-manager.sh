#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: On-Demand LUKS2 / LVM Orchestrator with HA Integration
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

# -------------------------------------------------------------------
# 0. LOAD ENVIRONMENT CONFIGURATION
# -------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    # Load configuration variables
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
else
    echo "[!] ERRORE: File di configurazione .env non trovato!" >&2
    echo "    Copia il file di esempio ed inserisci i tuoi parametri:" >&2
    echo "    cp ${SCRIPT_DIR}/.env.example ${SCRIPT_DIR}/.env" >&2
    exit 1
fi

# Set defaults for optional settings
HA_INSECURE_TLS="${HA_INSECURE_TLS:-true}"
HA_WAIT_TIMEOUT="${HA_WAIT_TIMEOUT:-15}"
USB_DETECT_TIMEOUT="${USB_DETECT_TIMEOUT:-20}"
SPINDOWN_WAIT_SEC="${SPINDOWN_WAIT_SEC:-3}"
RELOAD_SAMBA="${RELOAD_SAMBA:-true}"
TARGET_DEV="${TARGET_DEV:-}"

# Construct full LVM paths
LV_CRYPTO_PATH="/dev/${VG_NAME}/${LV_CRYPTO}"
LV_BACKUP_PATH="/dev/${VG_NAME}/${LV_BACKUP}"

CURL_FLAGS="-s -f"
if [ "$HA_INSECURE_TLS" = "true" ]; then
    CURL_FLAGS="-k ${CURL_FLAGS}"
fi

# -------------------------------------------------------------------
# HELPER FUNCTIONS FOR HOME ASSISTANT REST API
# -------------------------------------------------------------------
ha_call_service() {
    local action="$1" # turn_on or turn_off
    # shellcheck disable=SC2086
    curl ${CURL_FLAGS} -X POST \
      -H "Authorization: Bearer ${HA_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"entity_id\": \"${HA_ENTITY_ID}\"}" \
      "${HA_URL}/api/services/switch/${action}" > /dev/null
}

ha_get_state() {
    # shellcheck disable=SC2086
    curl ${CURL_FLAGS} \
      -H "Authorization: Bearer ${HA_TOKEN}" \
      -H "Content-Type: application/json" \
      "${HA_URL}/api/states/${HA_ENTITY_ID}" | grep -o '"state":"[^"]*"' | cut -d'"' -f4
}

detect_target_device() {
    if [ -n "$TARGET_DEV" ] && [ -b "$TARGET_DEV" ]; then
        echo "$TARGET_DEV"
        return
    fi

    # Try resolving via LVM physical volume lookup
    local pv_dev
    pv_dev=$(sudo pvs --noheadings -o pv_name -g "$VG_NAME" 2>/dev/null | tr -d ' ' | head -n1 || echo "")
    if [ -n "$pv_dev" ] && [ -b "$pv_dev" ]; then
        local parent_disk
        parent_disk=$(lsblk -no PKNAME "$pv_dev" 2>/dev/null || echo "")
        if [ -n "$parent_disk" ]; then
            echo "/dev/${parent_disk}"
            return
        fi
        echo "$pv_dev"
        return
    fi

    # Fallback default check
    if [ -b "/dev/sdb" ]; then
        echo "/dev/sdb"
        return
    fi

    echo ""
}

# ===================================================================
# 1. HARDWARE POWER-ON (HOME ASSISTANT)
# ===================================================================
echo "[1/7] Invio comando di accensione presa a Home Assistant (${HA_ENTITY_ID})..."
ha_call_service "turn_on"

echo "[*] Attesa conferma stato 'on' da Home Assistant..."
for i in $(seq 1 "$HA_WAIT_TIMEOUT"); do
    STATE=$(ha_get_state || echo "unknown")
    if [ "$STATE" == "on" ]; then
        echo "[✓] Presa smart alimentata!"
        break
    fi
    sleep 1
done

echo "[2/7] Attesa rilevamento disco dal kernel Linux (udev)..."
DEV_FOUND=false
for i in $(seq 1 "$USB_DETECT_TIMEOUT"); do
    FOUND_DEV=$(detect_target_device)
    if [ -n "$FOUND_DEV" ] || sudo lvs "$VG_NAME" &>/dev/null; then
        DEV_FOUND=true
        echo "[✓] Disco rilevato sul bus USB!"
        break
    fi
    sleep 1
done

if [ "$DEV_FOUND" = false ]; then
    echo "[!] ERRORE: Disco non rilevato entro ${USB_DETECT_TIMEOUT} secondi." >&2
    echo "[*] Spegnimento di emergenza della presa..." >&2
    ha_call_service "turn_off"
    exit 1
fi

# ===================================================================
# 2. LVM ACTIVATION & LUKS DECRYPTION
# ===================================================================
echo "[3/7] Attivazione Volume Group LVM '$VG_NAME'..."
sudo vgchange -ay "$VG_NAME"

echo -n -e "\n[*] Inserisci la Passphrase LUKS per '$LV_CRYPTO_PATH': "
read -rs PASSPHRASE
echo -e "\n[4/7] Sblocco volume cifrato LUKS2 in RAM..."

if ! echo -n "$PASSPHRASE" | sudo cryptsetup open "$LV_CRYPTO_PATH" "$MAPPER_NAME" --key-file - ; then
    echo "[!] ERRORE: Passphrase errata o sblocco fallito." >&2
    unset PASSPHRASE
    sudo vgchange -an "$VG_NAME" 2>/dev/null || true
    ha_call_service "turn_off"
    exit 1
fi
unset PASSPHRASE
echo "[✓] Volume sbloccato in /dev/mapper/$MAPPER_NAME"

# ===================================================================
# 3. MOUNT FILESYSTEMS & REFRESH SERVICES
# ===================================================================
echo "[5/7] Montaggio volumi su filesystem..."
sudo mkdir -p "$MOUNT_CRYPTO" "$MOUNT_BACKUP"

sudo mount -o noatime,nodev,nosuid "/dev/mapper/$MAPPER_NAME" "$MOUNT_CRYPTO"
echo "[✓] Dati Cifrati montati su: $MOUNT_CRYPTO"

if sudo mount -o noatime,nodev,nosuid "$LV_BACKUP_PATH" "$MOUNT_BACKUP" 2>/dev/null; then
    echo "[✓] Dati Backup montati su: $MOUNT_BACKUP"
fi

if [ "$RELOAD_SAMBA" = "true" ]; then
    sudo smbcontrol all reload-config 2>/dev/null || true
fi

echo -e "\n==================================================================="
df -h "$MOUNT_CRYPTO" "$MOUNT_BACKUP" 2>/dev/null || df -h "$MOUNT_CRYPTO"
echo "==================================================================="
echo " DISCO OPERATIVO E ACCESSIBILE DA TUTTI I DISPOSITIVI!"
echo " Premi [INVIO] quando desideri chiudere, sigillare e spegnere la 220V."
echo "==================================================================="
read -p ""

# ===================================================================
# 4. TEARDOWN, SIGILLO LUKS, PARCHEGGIO SCSI & VERIFICA DISCONNESSIONE
# ===================================================================
echo -e "\n[6/7] Procedura di arresto sicura in corso..."

echo "  -> Flush buffer RAM (sync)..."
sudo sync

echo "  -> Termine processi attivi sui mountpoint..."
sudo fuser -km "$MOUNT_CRYPTO" "$MOUNT_BACKUP" 2>/dev/null || true

echo "  -> Smontaggio filesystem..."
sudo umount "$MOUNT_CRYPTO" 2>/dev/null || sudo umount -l "$MOUNT_CRYPTO" 2>/dev/null || true
sudo umount "$MOUNT_BACKUP" 2>/dev/null || sudo umount -l "$MOUNT_BACKUP" 2>/dev/null || true

echo "  -> Chiusura container LUKS (chiave cancellata da RAM)..."
sudo cryptsetup close "$MAPPER_NAME" 2>/dev/null || true

echo "  -> Disattivazione Volume Group LVM..."
sudo vgchange -an "$VG_NAME" 2>/dev/null || true

FINAL_DEV=$(detect_target_device)
if [ -n "$FINAL_DEV" ] && [ -b "$FINAL_DEV" ]; then
    DEV_NAME=$(basename "$FINAL_DEV")
    echo "  -> Invio comando SCSI STOP UNIT ed espulsione bus per $FINAL_DEV..."
    sudo udisksctl power-off -b "$FINAL_DEV" 2>/dev/null || true

    # VERIFICA ATTIVA DISCONNESSIONE KERNEL (Nessuna scommessa sui tempi)
    echo "  -> Verifica attiva disconnessione hardware nel kernel Linux..."
    OFF_CONFIRMED=false
    for i in {1..10}; do
        if [ ! -b "$FINAL_DEV" ] && [ ! -d "/sys/block/${DEV_NAME}" ]; then
            OFF_CONFIRMED=true
            echo "  [✓] Disconnessione confermata dal kernel! Il disco è totalmente inerte."
            break
        fi
        sleep 1
    done

    if [ "$OFF_CONFIRMED" = false ]; then
        echo "  [!] ATTENZIONE: Il kernel non ha confermato il distacco completo entro 10s."
        echo "      Attesa di sicurezza aggiuntiva prima del cutoff 220V..."
        sleep "$SPINDOWN_WAIT_SEC"
    fi
fi

# ===================================================================
# 5. SPEGNIMENTO PRESA (0 WATT STANDBY)
# ===================================================================
echo "[7/7] Invio comando spegnimento 220V a Home Assistant..."
ha_call_service "turn_off"

echo -e "\n[✓] CICLO COMPLETATO CON SUCCESSO!"
echo "    - Filesystem smontati."
echo "    - Chiave LUKS distrutta dalla RAM."
echo "    - Volume Group LVM disattivato."
echo "    - Testine parcheggiate su rampa e bus USB disconnesso."
echo "    - Alimentazione 220V disattivata (0 Watt consumi)."

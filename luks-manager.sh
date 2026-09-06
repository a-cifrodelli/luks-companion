#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: On-Demand LUKS2 / LVM Orchestrator with HA Integration
# ===================================================================
set -euo pipefail

# -------------------------------------------------------------------
# 0. ROOT PRIVILEGE CHECK (ASK ROOT PASSWORD AT THE VERY START)
# -------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "[*] Richiesta autenticazione di amministrazione (sudo)..."
    exec sudo "$0" "$@"
fi

# -------------------------------------------------------------------
# 0B. BUFFER STDIN IMMEDIATELY (INTO RAM TMPFS) TO PREVENT CORRUPTION
# -------------------------------------------------------------------
STDIN_KEY_FILE=""
if [ ! -t 0 ]; then
    STDIN_KEY_FILE=$(mktemp /dev/shm/luks_key.XXXXXX 2>/dev/null || mktemp /tmp/luks_key.XXXXXX)
    chmod 600 "$STDIN_KEY_FILE"
    cat > "$STDIN_KEY_FILE"
fi

cleanup_key() {
    if [ -n "$STDIN_KEY_FILE" ] && [ -f "$STDIN_KEY_FILE" ]; then
        shred -u "$STDIN_KEY_FILE" 2>/dev/null || rm -f "$STDIN_KEY_FILE"
        STDIN_KEY_FILE=""
    fi
}

# -------------------------------------------------------------------
# 0C. EXCLUSIVE EXECUTION LOCKING (FLOCK)
# -------------------------------------------------------------------
LOCK_FILE="/run/luks-manager.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[!] ERRORE: Un'altra istanza di luks-manager è già in esecuzione!" >&2
    echo "    Attendere la chiusura della sessione attiva prima di avviarne un'altra." >&2
    cleanup_key
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

# -------------------------------------------------------------------
# 1. LOAD ENVIRONMENT CONFIGURATION
# -------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
else
    echo "[!] ERRORE: File di configurazione .env non trovato!" >&2
    echo "    Copia il file di esempio ed inserisci i tuoi parametri:" >&2
    echo "    cp ${SCRIPT_DIR}/.env.example ${SCRIPT_DIR}/.env" >&2
    cleanup_key
    exit 1
fi

# Set defaults for optional settings
HA_INSECURE_TLS="${HA_INSECURE_TLS:-true}"
HA_WAIT_TIMEOUT="${HA_WAIT_TIMEOUT:-15}"
USB_DETECT_TIMEOUT="${USB_DETECT_TIMEOUT:-40}"
MAX_PASSPHRASE_TRIES="${MAX_PASSPHRASE_TRIES:-3}"
SPINDOWN_WAIT_SEC="${SPINDOWN_WAIT_SEC:-3}"
CUTOFF_GRACE_SEC="${CUTOFF_GRACE_SEC:-5}"
IDLE_TIMEOUT_MIN="${IDLE_TIMEOUT_MIN:-30}"
ENABLE_WEBDAV="${ENABLE_WEBDAV:-true}"
RELOAD_SAMBA="${RELOAD_SAMBA:-false}"
TARGET_DEV="${TARGET_DEV:-}"
LV_BACKUP="${LV_BACKUP:-}"
MOUNT_BACKUP="${MOUNT_BACKUP:-}"

WEBDAV_SHARE_DIR="${WEBDAV_SCOPE:-/srv/webdav}"

# Construct full LVM paths
LV_CRYPTO_PATH="/dev/${VG_NAME}/${LV_CRYPTO}"

LV_BACKUP_PATH=""
if [ -n "$LV_BACKUP" ]; then
    LV_BACKUP_PATH="/dev/${VG_NAME}/${LV_BACKUP}"
fi

CURL_FLAGS="-s -f"
if [ "$HA_INSECURE_TLS" = "true" ]; then
    CURL_FLAGS="-k ${CURL_FLAGS}"
fi

# Parse positional arguments and flags
COMMAND="start"
NO_WATCHDOG=false
CLI_KEYFILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        start|unlock)
            COMMAND="start"
            shift
            ;;
        stop|lock)
            COMMAND="stop"
            shift
            ;;
        status)
            COMMAND="status"
            shift
            ;;
        --keyfile)
            if [ -n "${2:-}" ]; then
                CLI_KEYFILE="$2"
                shift 2
            else
                echo "[!] ERRORE: Opzione --keyfile richiede un percorso." >&2
                cleanup_key
                exit 1
            fi
            ;;
        --no-watchdog|--daemon)
            NO_WATCHDOG=true
            shift
            ;;
        -h|--help)
            echo "Uso: $0 [start|stop|status] [--keyfile <percorso_chiave>] [--no-watchdog]"
            cleanup_key
            exit 0
            ;;
        *)
            echo "[!] Argomento sconosciuto: $1" >&2
            echo "Uso: $0 [start|stop|status] [--keyfile <percorso_chiave>] [--no-watchdog]" >&2
            cleanup_key
            exit 1
            ;;
    esac
done

# Global state flags for cleanup trap
POWER_IS_ON=false
VOLUME_IS_UNLOCKED=false
FILESYSTEM_IS_MOUNTED=false
PASSPHRASE=""
TEARDOWN_DONE=false

# -------------------------------------------------------------------
# HELPER FUNCTIONS & SAFETY CHECKS
# -------------------------------------------------------------------
ha_call_service() {
    local action="$1" # turn_on or turn_off
    # shellcheck disable=SC2086
    if ! curl ${CURL_FLAGS} -X POST \
      -H "Authorization: Bearer ${HA_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"entity_id\": \"${HA_ENTITY_ID}\"}" \
      "${HA_URL}/api/services/switch/${action}" < /dev/null > /dev/null 2>&1; then
        echo "[!] WARNING: Impossibile contattare Home Assistant (azione: ${action}). Check rete/token." >&2
        return 1
    fi
    return 0
}

ha_get_state() {
    local res
    # shellcheck disable=SC2086
    res=$(curl ${CURL_FLAGS} \
      -H "Authorization: Bearer ${HA_TOKEN}" \
      -H "Content-Type: application/json" \
      "${HA_URL}/api/states/${HA_ENTITY_ID}" < /dev/null 2>/dev/null | grep -o '"state":"[^"]*"' | cut -d'"' -f4 || echo "unknown")
    echo "${res:-unknown}"
}

is_system_disk() {
    local target="$1"
    if [ -z "$target" ] || [ ! -b "$target" ]; then
        return 1
    fi

    local target_real
    target_real=$(readlink -f "$target" 2>/dev/null || echo "$target")
    local target_name
    target_name=$(basename "$target_real")

    # Check root mount (/), boot mount (/boot), and system partitions
    local sys_srcs
    sys_srcs=$(findmnt -n -o SOURCE / /boot /boot/firmware 2>/dev/null || echo "")

    for ssrc in $sys_srcs; do
        if [ -z "$ssrc" ]; then continue; fi
        local ssrc_real
        ssrc_real=$(readlink -f "$ssrc" 2>/dev/null || echo "$ssrc")
        local ssrc_parent
        ssrc_parent=$(lsblk -no PKNAME "$ssrc_real" 2>/dev/null || echo "")

        if [ "$target_real" = "$ssrc_real" ] || \
           [ "$target_real" = "/dev/${ssrc_parent}" ] || \
           [ "$target_name" = "$ssrc_parent" ]; then
            return 0 # YES: THIS IS THE RASPBERRY PI OS SYSTEM DISK!
        fi
    done

    return 1 # Safe: Not a system disk
}

detect_target_device() {
    # 1. Resolve strictly via LVM Physical Volume for VG_NAME (completely silent)
    local pv_dev
    pv_dev=$(pvs --noheadings -o pv_name -g "$VG_NAME" >/dev/null 2>&1 | tr -d ' ' | head -n1 || echo "")
    if [ -z "$pv_dev" ]; then
        pv_dev=$(pvs --noheadings -o pv_name 2>/dev/null | tr -d ' ' | head -n1 || echo "")
    fi

    if [ -n "$pv_dev" ] && [ -b "$pv_dev" ]; then
        local parent_disk
        parent_disk=$(lsblk -no PKNAME "$pv_dev" 2>/dev/null || echo "")
        if [ -n "$parent_disk" ] && [ -b "/dev/${parent_disk}" ]; then
            echo "/dev/${parent_disk}"
            return
        fi
        echo "$pv_dev"
        return
    fi

    # 2. Check TARGET_DEV ONLY if set AND verified to contain VG_NAME AND not system disk
    if [ -n "$TARGET_DEV" ] && [ -b "$TARGET_DEV" ]; then
        if pvs "$TARGET_DEV" >/dev/null 2>&1 | grep -q "$VG_NAME"; then
            if ! is_system_disk "$TARGET_DEV"; then
                echo "$TARGET_DEV"
                return
            fi
        fi
    fi

    echo ""
}

get_disk_io_stats() {
    local dev="$1"
    if [ -z "$dev" ] || [ ! -b "$dev" ]; then
        echo "0 0"
        return
    fi
    local dev_name
    dev_name=$(basename "$dev")
    if [ -f "/sys/block/${dev_name}/stat" ]; then
        awk '{print $1, $5}' "/sys/block/${dev_name}/stat" 2>/dev/null || echo "0 0"
    else
        echo "0 0"
    fi
}

safe_power_off_sequence() {
    # Ignore signals during teardown to guarantee atomic execution
    trap '' SIGINT SIGTERM SIGHUP
    cleanup_key

    if [ "$TEARDOWN_DONE" = true ]; then
        return 0
    fi
    TEARDOWN_DONE=true

    echo -e "\n[*] Esecuzione procedura di arresto e teardown sicuro..."

    if [ "$ENABLE_WEBDAV" = "true" ]; then
        echo "  -> Stop del servizio WebDAV..."
        systemctl stop webdav 2>/dev/null || true

        echo "  -> Smontaggio bind-mount WebDAV..."
        if [ -d "$WEBDAV_SHARE_DIR" ]; then
            umount -l "$WEBDAV_SHARE_DIR"/* 2>/dev/null || true
            rm -rf "${WEBDAV_SHARE_DIR:?}"/* 2>/dev/null || true
        fi
    fi

    echo "  -> Flush buffer RAM (sync)..."
    sync

    if [ "$FILESYSTEM_IS_MOUNTED" = true ] || mountpoint -q "$MOUNT_CRYPTO" 2>/dev/null; then
        echo "  -> Termine processi attivi sui mountpoint..."
        if mountpoint -q "$MOUNT_CRYPTO" 2>/dev/null; then
            fuser -km -9 "$MOUNT_CRYPTO" 2>/dev/null || true
        fi
        if [ -n "$MOUNT_BACKUP" ] && mountpoint -q "$MOUNT_BACKUP" 2>/dev/null; then
            fuser -km -9 "$MOUNT_BACKUP" 2>/dev/null || true
        fi

        sleep 1

        echo "  -> Smontaggio filesystem..."
        if mountpoint -q "$MOUNT_CRYPTO" 2>/dev/null; then
            umount "$MOUNT_CRYPTO" 2>/dev/null || {
                sleep 1
                fuser -km -9 "$MOUNT_CRYPTO" 2>/dev/null || true
                umount "$MOUNT_CRYPTO" 2>/dev/null || umount -l "$MOUNT_CRYPTO" 2>/dev/null || true
            }
        fi

        if [ -n "$MOUNT_BACKUP" ] && mountpoint -q "$MOUNT_BACKUP" 2>/dev/null; then
            umount "$MOUNT_BACKUP" 2>/dev/null || {
                sleep 1
                fuser -km -9 "$MOUNT_BACKUP" 2>/dev/null || true
                umount "$MOUNT_BACKUP" 2>/dev/null || umount -l "$MOUNT_BACKUP" 2>/dev/null || true
            }
        fi
        FILESYSTEM_IS_MOUNTED=false
    fi

    if [ "$VOLUME_IS_UNLOCKED" = true ] || [ -b "/dev/mapper/${MAPPER_NAME}" ]; then
        echo "  -> Chiusura container LUKS (chiave cancellata da RAM)..."
        for retry in 1 2 3; do
            if cryptsetup close "$MAPPER_NAME" 2>/dev/null; then
                echo "  [✓] Container LUKS chiuso."
                break
            fi
            sleep 1
        done
        VOLUME_IS_UNLOCKED=false
    fi

    echo "  -> Disattivazione Volume Group LVM..."
    for retry in 1 2 3; do
        if vgchange -an "$VG_NAME" >/dev/null 2>&1; then
            echo "  [✓] Volume Group LVM disattivato."
            break
        fi
        sleep 1
    done

    local final_dev
    final_dev=$(detect_target_device)
    if [ -n "$final_dev" ] && [ -b "$final_dev" ]; then
        if is_system_disk "$final_dev"; then
            echo "  [!] PROTEZIONE ATTIVA: $final_dev è il disco di sistema OS del Raspberry Pi! Comandi SCSI spegnimento annullati." >&2
        else
            local dev_name
            dev_name=$(basename "$final_dev")
            echo "  -> Invio comando SCSI STOP UNIT ed espulsione bus per $final_dev..."
            udisksctl power-off -b "$final_dev" 2>/dev/null || true

            echo "  -> Verifica attiva disconnessione hardware nel kernel Linux..."
            local off_confirmed=false
            for i in {1..10}; do
                if [ ! -b "$final_dev" ] && [ ! -d "/sys/block/${dev_name}" ]; then
                    off_confirmed=true
                    echo "  [✓] Disconnessione confermata dal kernel! Il disco è inerte."
                    break
                fi
                sleep 1
            done

            if [ "$off_confirmed" = false ]; then
                echo "  [!] Attesa di sicurezza aggiuntiva prima del cutoff 220V..."
                sleep "$SPINDOWN_WAIT_SEC"
            fi
        fi
    else
        echo "  [*] Nessun disco esterno LVM rilevato da disconnettere via SCSI."
    fi

    # Cutoff 220V Smart Plug if power was turned on OR if HA reports plug is currently ON
    local ha_current_state
    ha_current_state=$(ha_get_state)
    if [ "$POWER_IS_ON" = true ] || [ "$ha_current_state" == "on" ]; then
        echo "  -> Pausa di tolleranza pre-cutoff (${CUTOFF_GRACE_SEC}s)..."
        sleep "$CUTOFF_GRACE_SEC"

        echo "  -> Invio comando spegnimento 220V a Home Assistant..."
        ha_call_service "turn_off" || true
        POWER_IS_ON=false
    fi

    # Erase passphrase from script variable
    PASSPHRASE=""
}

# -------------------------------------------------------------------
# ACTION: STATUS
# -------------------------------------------------------------------
if [ "$COMMAND" = "status" ]; then
    echo "=== LUKS MANAGER: STATO ATTUALE ==="
    ha_st=$(ha_get_state)
    echo "  - Alimentazione Presa HA: ${ha_st}"
    
    if [ -d "/dev/${VG_NAME}" ] || lvs "$VG_NAME" >/dev/null 2>&1; then
        echo "  - Volume Group LVM ($VG_NAME): ATTIVO"
    else
        echo "  - Volume Group LVM ($VG_NAME): NON ATTIVO"
    fi

    if [ -b "/dev/mapper/${MAPPER_NAME}" ]; then
        echo "  - LUKS Mapper (/dev/mapper/${MAPPER_NAME}): SBLOCCATO"
    else
        echo "  - LUKS Mapper (/dev/mapper/${MAPPER_NAME}): BLOCCATO"
    fi

    if mountpoint -q "$MOUNT_CRYPTO" 2>/dev/null; then
        echo "  - Mount Principale ($MOUNT_CRYPTO): MONTATO"
    else
        echo "  - Mount Principale ($MOUNT_CRYPTO): NON MONTATO"
    fi

    if [ -n "$MOUNT_BACKUP" ]; then
        if mountpoint -q "$MOUNT_BACKUP" 2>/dev/null; then
            echo "  - Mount Secondario ($MOUNT_BACKUP): MONTATO"
        else
            echo "  - Mount Secondario ($MOUNT_BACKUP): NON MONTATO"
        fi
    fi

    if systemctl is-active --quiet webdav 2>/dev/null; then
        echo "  - Servizio WebDAV: ATTIVO"
    else
        echo "  - Servizio WebDAV: NON ATTIVO"
    fi
    cleanup_key
    exit 0
fi

# -------------------------------------------------------------------
# ACTION: STOP / LOCK
# -------------------------------------------------------------------
if [ "$COMMAND" = "stop" ]; then
    echo "=== LUKS MANAGER: ARRESTO E SMONTAGGIO RICHIESTO ==="
    safe_power_off_sequence
    echo -e "\n[✓] ARRESTO COMPLETATO CON SUCCESSO!"
    echo "    - Filesystem smontati."
    echo "    - Chiave LUKS distrutta dalla RAM."
    echo "    - Volume Group LVM disattivato."
    echo "    - Testine parcheggiate su rampa e bus USB disconnesso."
    echo "    - Alimentazione 220V disattivata (0 Watt consumi)."
    exit 0
fi

# -------------------------------------------------------------------
# ACTION: START / UNLOCK
# -------------------------------------------------------------------

# Register trap for clean shutdown on interrupt
trap_cleanup() {
    echo -e "\n\n[!] Interruzione rilevata (Ctrl+C / Segnale di uscita)!" >&2
    safe_power_off_sequence
    exit 130
}
trap trap_cleanup SIGINT SIGTERM SIGHUP

# ===================================================================
# 1. HARDWARE POWER-ON (HOME ASSISTANT)
# ===================================================================
echo "=== LUKS MANAGER: AVVIO SISTEMA STOC CUSTODITO ==="
echo -e "\n[1/7] Invio comando di accensione presa a Home Assistant (${HA_ENTITY_ID})..."
ha_call_service "turn_on" || true
POWER_IS_ON=true

echo "[*] Attesa conferma stato 'on' da Home Assistant..."
for i in $(seq 1 "$HA_WAIT_TIMEOUT"); do
    STATE=$(ha_get_state)
    if [ "$STATE" == "on" ]; then
        echo "[✓] Presa smart alimentata!"
        break
    fi
    sleep 1
done

# ===================================================================
# 2. WAIT FOR USB DISK & LVM DETECTION (QUIET / SILENT)
# ===================================================================
echo "[2/7] Attesa rilevamento disco dal kernel Linux (udev, max ${USB_DETECT_TIMEOUT}s)..."
DEV_FOUND=false
for i in $(seq 1 "$USB_DETECT_TIMEOUT"); do
    pvscan < /dev/null >/dev/null 2>&1 || true
    vgscan --mknodes < /dev/null >/dev/null 2>&1 || true
    FOUND_DEV=$(detect_target_device)
    if [ -n "$FOUND_DEV" ] || lvs "$VG_NAME" < /dev/null >/dev/null 2>&1; then
        DEV_FOUND=true
        echo "[✓] Disco rilevato sul bus USB!"
        break
    fi
    sleep 1
done

if [ "$DEV_FOUND" = false ]; then
    echo "[!] ERRORE: Disco non rilevato entro ${USB_DETECT_TIMEOUT} secondi." >&2
    safe_power_off_sequence
    exit 1
fi

# ===================================================================
# 3. LVM ACTIVATION
# ===================================================================
echo "[3/7] Attivazione Volume Group LVM '$VG_NAME'..."
vgscan --mknodes < /dev/null >/dev/null 2>&1 || true
vgchange -ay "$VG_NAME" < /dev/null >/dev/null 2>&1 || vgchange -ay "$VG_NAME" < /dev/null

# ===================================================================
# 4. LUKS DECRYPTION IN-MEMORY (RAM / STDIN / KEYFILE)
# ===================================================================
echo "[4/7] Sblocco volume cifrato LUKS2 in RAM..."

# Check if LUKS mapper is already open from a stale session
if [ -b "/dev/mapper/${MAPPER_NAME}" ]; then
    echo "[*] Container LUKS già aperto in /dev/mapper/$MAPPER_NAME."
    VOLUME_IS_UNLOCKED=true
    cleanup_key
else
    VOLUME_IS_UNLOCKED=false

    # Scenario A: Explicit Keyfile provided via CLI flag
    if [ -n "$CLI_KEYFILE" ]; then
        if [ ! -f "$CLI_KEYFILE" ]; then
            echo "[!] ERRORE: File chiave specificato '$CLI_KEYFILE' non trovato!" >&2
            safe_power_off_sequence
            exit 1
        fi
        echo "[*] Sblocco con file chiave: $CLI_KEYFILE..."
        if cryptsetup open "$LV_CRYPTO_PATH" "$MAPPER_NAME" --key-file "$CLI_KEYFILE" >/dev/null 2>&1; then
            VOLUME_IS_UNLOCKED=true
            echo "[✓] Volume sbloccato con successo tramite keyfile!"
        else
            echo "[!] ERRORE: Chiave non valida per '$LV_CRYPTO_PATH'." >&2
            safe_power_off_sequence
            exit 1
        fi

    # Scenario B: Stdin Key File (buffered safely from daemon / client)
    elif [ -n "$STDIN_KEY_FILE" ] && [ -s "$STDIN_KEY_FILE" ]; then
        if cryptsetup open "$LV_CRYPTO_PATH" "$MAPPER_NAME" --key-file "$STDIN_KEY_FILE" >/dev/null 2>&1; then
            VOLUME_IS_UNLOCKED=true
            cleanup_key
            echo "[✓] Volume sbloccato con successo da stdin in /dev/mapper/$MAPPER_NAME"
        else
            cleanup_key
            echo "[!] ERRORE: Chiave/Passphrase da stdin non valida o errata." >&2
            safe_power_off_sequence
            exit 1
        fi

    # Scenario C: Interactive TTY Passphrase prompt
    elif [ -t 0 ]; then
        for try in $(seq 1 "$MAX_PASSPHRASE_TRIES"); do
            echo -n -e "\n[*] Inserisci la Passphrase LUKS per '$LV_CRYPTO_PATH' (tentativo $try di $MAX_PASSPHRASE_TRIES): "
            read -rs PASSPHRASE
            echo ""

            if [ -z "$PASSPHRASE" ]; then
                echo "[!] Passphrase vuota non valida. Riprova..." >&2
                continue
            fi

            if printf '%s' "$PASSPHRASE" | cryptsetup open "$LV_CRYPTO_PATH" "$MAPPER_NAME" --key-file - >/dev/null 2>&1; then
                VOLUME_IS_UNLOCKED=true
                PASSPHRASE=""
                echo "[✓] Volume sbloccato con successo in /dev/mapper/$MAPPER_NAME"
                break
            else
                PASSPHRASE=""
                echo "[!] ERRORE: Passphrase LUKS errata." >&2
            fi
        done
    else
        echo "[!] ERRORE: Nessun input chiave o passphrase ricevuto da stdin." >&2
        safe_power_off_sequence
        exit 1
    fi
fi

if [ "$VOLUME_IS_UNLOCKED" = false ]; then
    echo -e "\n[!] ERRORE: Impossibile sbloccare il container LUKS." >&2
    echo "    Esecuzione spegnimento di sicurezza della presa e pulizia..." >&2
    safe_power_off_sequence
    exit 1
fi

# ===================================================================
# 5. MOUNT FILESYSTEMS & REFRESH SERVICES
# ===================================================================
echo "[5/7] Montaggio volumi su filesystem..."
mkdir -p "$MOUNT_CRYPTO"
if ! mountpoint -q "$MOUNT_CRYPTO"; then
    mount -o noatime,nodev,nosuid "/dev/mapper/$MAPPER_NAME" "$MOUNT_CRYPTO"
fi
FILESYSTEM_IS_MOUNTED=true
echo "[✓] Dati Cifrati montati su: $MOUNT_CRYPTO"

if [ -n "$LV_BACKUP_PATH" ] && [ -n "$MOUNT_BACKUP" ]; then
    mkdir -p "$MOUNT_BACKUP"
    if ! mountpoint -q "$MOUNT_BACKUP"; then
        mount -o noatime,nodev,nosuid "$LV_BACKUP_PATH" "$MOUNT_BACKUP" 2>/dev/null || true
    fi
    echo "[✓] Dati Secondo Volume montati su: $MOUNT_BACKUP"
fi

if [ "$ENABLE_WEBDAV" = "true" ]; then
    echo "[*] Configurazione ambito WebDAV isolato in ${WEBDAV_SHARE_DIR}..."
    mkdir -p "$WEBDAV_SHARE_DIR"
    umount -l "$WEBDAV_SHARE_DIR"/* 2>/dev/null || true
    rm -rf "${WEBDAV_SHARE_DIR:?}"/* 2>/dev/null || true

    # Bind mount primary mount point
    crypto_folder_name=$(basename "$MOUNT_CRYPTO")
    mkdir -p "${WEBDAV_SHARE_DIR}/${crypto_folder_name}"
    mount --bind "$MOUNT_CRYPTO" "${WEBDAV_SHARE_DIR}/${crypto_folder_name}"

    # Bind mount secondary mount point if configured and mounted
    if [ -n "$MOUNT_BACKUP" ] && mountpoint -q "$MOUNT_BACKUP"; then
        backup_folder_name=$(basename "$MOUNT_BACKUP")
        mkdir -p "${WEBDAV_SHARE_DIR}/${backup_folder_name}"
        mount --bind "$MOUNT_BACKUP" "${WEBDAV_SHARE_DIR}/${backup_folder_name}"
    fi

    if [ -f "/etc/webdav/config.yaml" ]; then
        sed -i "s|directory: \".*\"|directory: \"${WEBDAV_SHARE_DIR}\"|g" /etc/webdav/config.yaml 2>/dev/null || true
        sed -i "s|scope: \".*\"|scope: \"${WEBDAV_SHARE_DIR}\"|g" /etc/webdav/config.yaml 2>/dev/null || true
    fi

    echo "[*] Avvio/Riavvio del servizio WebDAV..."
    systemctl restart webdav 2>/dev/null || systemctl start webdav 2>/dev/null || echo "[!] WARNING: Impossibile avviare webdav.service" >&2
    echo "[✓] Server WebDAV attivo ed isolato sulle sole cartelle di mount!"
fi

if [ "$RELOAD_SAMBA" = "true" ]; then
    smbcontrol all reload-config 2>/dev/null || true
fi

echo -e "\n==================================================================="
if [ -n "$MOUNT_BACKUP" ]; then
    df -h "$MOUNT_CRYPTO" "$MOUNT_BACKUP" 2>/dev/null || df -h "$MOUNT_CRYPTO"
else
    df -h "$MOUNT_CRYPTO"
fi
echo "==================================================================="

# In non-interactive mode or --no-watchdog / daemon mode, exit cleanly now!
if [ "$NO_WATCHDOG" = true ] || [ ! -t 0 ]; then
    echo " DISCO OPERATIVO E MONTATO CON SUCCESSO!"
    echo " (Esecuzione non-interattiva/demone: sessione attiva lasciata montata)."
    echo "==================================================================="
    # Disarm trap so we don't power off on exit
    trap - SIGINT SIGTERM SIGHUP
    exit 0
fi

echo " DISCO OPERATIVO E ACCESSIBILE DA TUTTI I DISPOSITIVI!"
if [ "$IDLE_TIMEOUT_MIN" -gt 0 ]; then
    echo " Watchdog di inattività attivo: Spegnimento automatico dopo ${IDLE_TIMEOUT_MIN} min di inattività I/O."
fi
echo " Premi [INVIO] in qualsiasi momento per chiudere, sigillare e spegnere la 220V."
echo "==================================================================="

# Interactive Watchdog & Session Loop
if [ "$IDLE_TIMEOUT_MIN" -gt 0 ]; then
    target_disk=$(detect_target_device)
    last_stats=$(get_disk_io_stats "$target_disk")
    idle_seconds=0
    check_interval=15
    max_idle_seconds=$((IDLE_TIMEOUT_MIN * 60))

    while true; do
        if [ -t 0 ]; then
            if read -r -t "$check_interval" -p "" 2>/dev/null; then
                echo "[*] Chiusura manuale richiesta dall'utente (INVIO)..."
                break
            fi
        else
            sleep "$check_interval"
        fi

        current_stats=$(get_disk_io_stats "$target_disk")
        if [ "$current_stats" == "$last_stats" ]; then
            idle_seconds=$((idle_seconds + check_interval))
            idle_min=$((idle_seconds / 60))
            if [ "$idle_seconds" -ge "$max_idle_seconds" ]; then
                echo -e "\n[!] WATCHDOG: Inattività I/O sul disco rilevata per ${idle_min} minuti (${IDLE_TIMEOUT_MIN}m max)."
                echo "    Avvio procedura di smontaggio e spegnimento automatico..."
                break
            fi
        else
            idle_seconds=0
            last_stats="$current_stats"
        fi
    done
else
    if [ -t 0 ]; then
        read -r -p "" || true
    fi
fi

# Reset trap for normal clean exit
trap - SIGINT SIGTERM SIGHUP

# ===================================================================
# 6. TEARDOWN, SIGILLO LUKS, PARCHEGGIO SCSI & CUTOFF 220V
# ===================================================================
echo -e "\n[6/7] Esecuzione procedura di arresto sicura..."
safe_power_off_sequence

echo -e "\n[✓] CICLO COMPLETATO CON SUCCESSO!"
echo "    - Filesystem smontati."
echo "    - Chiave LUKS distrutta dalla RAM."
echo "    - Volume Group LVM disattivato."
echo "    - Testine parcheggiate su rampa e bus USB disconnesso."
echo "    - Alimentazione 220V disattivata (0 Watt consumi)."

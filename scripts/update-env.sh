#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated Environment Config (.env) Synchronizer
# - Removes deprecated keys no longer present in .env.example
# - Appends newly introduced configuration keys from .env.example into .env
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
EXAMPLE_FILE="${SCRIPT_DIR}/.env.example"

# If .env is root:root 600 and running unprivileged, auto-escalate with sudo
if [ -f "$ENV_FILE" ] && [ ! -w "$ENV_FILE" ] && [ "$(id -u)" -ne 0 ]; then
    if [ -t 0 ]; then
        exec sudo "$0" "$@"
    fi
fi

# ANSI Color Palette
CLR_RESET="\033[0m"
CLR_BOLD="\033[1m"
CLR_CYAN="\033[0;36m"
CLR_BCYAN="\033[1;36m"
CLR_GREEN="\033[1;32m"
CLR_YELLOW="\033[1;33m"
CLR_RED="\033[1;31m"

echo -e "${CLR_BCYAN}=== LUKS COMPANION: SINCRONIZZAZIONE CONFIGURAZIONE (.env) ===${CLR_RESET}"

if [ ! -f "$EXAMPLE_FILE" ]; then
    echo -e "${CLR_RED}[✗] ERRORE: File template .env.example non trovato in ${EXAMPLE_FILE}!${CLR_RESET}" >&2
    exit 1
fi

# If .env does not exist at all, copy from example
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${CLR_YELLOW}[!] File .env non presente. Creazione da .env.example...${CLR_RESET}"
    cp "$EXAMPLE_FILE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo -e "${CLR_GREEN}[✓] File .env creato con successo! Modificalo con i tuoi parametri.${CLR_RESET}"
    exit 0
fi

CHANGES_MADE=false

# -------------------------------------------------------------------
# 1. REMOVE DEPRECATED / OBSOLETE KEYS NO LONGER IN .env.example
# -------------------------------------------------------------------
DEPRECATED_KEYS=$(awk -F'=' 'NR==FNR{if (/^[A-Z0-9_]+=/){valid[$1]=1}; next} {if (/^[A-Z0-9_]+=/ && !valid[$1]){print $1}}' "$EXAMPLE_FILE" "$ENV_FILE")

if [ -n "$DEPRECATED_KEYS" ]; then
    echo -e "${CLR_YELLOW}[!] Rilevate chiavi obsolete o calcolate dinamicamente da rimuovere da .env:${CLR_RESET}"
    while IFS= read -r key; do
        if [ -n "$key" ]; then
            echo -e "  ${CLR_RED}- ${CLR_BOLD}${key}${CLR_RESET} (ora calcolata dinamicamente da STORAGE_BASE)"
            sed -i -E "/^[[:space:]]*${key}=/d" "$ENV_FILE"
            CHANGES_MADE=true
        fi
    done <<< "$DEPRECATED_KEYS"
    echo ""
fi

# -------------------------------------------------------------------
# 2. APPEND MISSING KEYS FROM .env.example
# -------------------------------------------------------------------
MISSING_VARS=$(awk -F'=' 'NR==FNR{if (/^[A-Z0-9_]+=/){keys[$1]=1}; next} {if (/^[A-Z0-9_]+=/ && !keys[$1]){print $0}}' "$ENV_FILE" "$EXAMPLE_FILE")

if [ -n "$MISSING_VARS" ]; then
    echo -e "${CLR_CYAN}[*] Rilevate nuove variabili in .env.example non presenti nel tuo .env:${CLR_RESET}"
    while IFS= read -r line; do
        if [ -n "$line" ]; then
            var_name=$(echo "$line" | cut -d'=' -f1)
            var_val=$(echo "$line" | cut -d'=' -f2-)
            echo -e "  ${CLR_GREEN}+ ${CLR_BOLD}${var_name}${CLR_RESET}=${var_val}"
        fi
    done <<< "$MISSING_VARS"

    echo -e "\n${CLR_CYAN}[*] Aggiunta automatica in coda a .env...${CLR_RESET}"
    {
        echo ""
        echo "# -------------------------------------------------------------------"
        echo "# Variabili aggiunte automaticamente da scripts/update-env.sh ($(date +'%Y-%m-%d %H:%M:%S'))"
        echo "# -------------------------------------------------------------------"
        echo "$MISSING_VARS"
    } >> "$ENV_FILE"
    CHANGES_MADE=true
fi

chmod 600 "$ENV_FILE"

if [ "$CHANGES_MADE" = true ]; then
    echo -e "\n${CLR_GREEN}[✓] File .env sincronizzato con successo!${CLR_RESET}"
    echo -e "${CLR_CYAN}[*] I punti di mount vengono ora calcolati automaticamente da STORAGE_BASE (/srv/storage/<mapper_name>).${CLR_RESET}"
else
    echo -e "${CLR_GREEN}[✓] Il tuo file .env è già perfettamente allineato con .env.example.${CLR_RESET}"
fi

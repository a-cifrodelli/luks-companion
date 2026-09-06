#!/usr/bin/env bash
# ===================================================================
# LUKS-MANAGER: Automated Environment Config (.env) Synchronizer
# Appends missing configuration keys from .env.example into .env
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
EXAMPLE_FILE="${SCRIPT_DIR}/.env.example"

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

# Find keys present in .env.example but missing in .env
MISSING_VARS=$(awk -F'=' 'NR==FNR{if (/^[A-Z0-9_]+=/){keys[$1]=1}; next} {if (/^[A-Z0-9_]+=/ && !keys[$1]){print $0}}' "$ENV_FILE" "$EXAMPLE_FILE")

if [ -z "$MISSING_VARS" ]; then
    echo -e "${CLR_GREEN}[✓] Il tuo file .env è già completamente aggiornato! Nessuna nuova variabile da aggiungere.${CLR_RESET}"
    exit 0
fi

echo -e "${CLR_CYAN}[*] Rilevate nuove variabili in .env.example non presenti nel tuo .env:${CLR_RESET}"
echo ""

while IFS= read -r line; do
    if [ -n "$line" ]; then
        var_name=$(echo "$line" | cut -d'=' -f1)
        var_val=$(echo "$line" | cut -d'=' -f2-)
        echo -e "  ${CLR_YELLOW}+ ${CLR_BOLD}${var_name}${CLR_RESET}=${var_val}"
    fi
done <<< "$MISSING_VARS"

echo ""
echo -e "${CLR_CYAN}[*] Aggiunta automatica in coda a .env...${CLR_RESET}"

{
    echo ""
    echo "# -------------------------------------------------------------------"
    echo "# Variabili aggiunte automaticamente da scripts/update-env.sh ($(date +'%Y-%m-%d %H:%M:%S'))"
    echo "# -------------------------------------------------------------------"
    echo "$MISSING_VARS"
} >> "$ENV_FILE"

chmod 600 "$ENV_FILE"
echo -e "${CLR_GREEN}[✓] File .env aggiornato con successo!${CLR_RESET}"
echo -e "${CLR_CYAN}[*] Ricorda di verificare ed eventualmente personalizzare i nuovi parametri in .env.${CLR_RESET}"

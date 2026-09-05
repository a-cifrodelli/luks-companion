#!/usr/bin/env bash
# ===================================================================
# LUKS-COMPANION: Standalone Home Assistant REST API Unit Test
# ===================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
else
    echo "[!] ERRORE: File .env non trovato!" >&2
    echo "    Copia .env.example in .env ed imposta i parametri." >&2
    exit 1
fi

HA_INSECURE_TLS="${HA_INSECURE_TLS:-true}"
CURL_FLAGS="-s -f"
if [ "$HA_INSECURE_TLS" = "true" ]; then
    CURL_FLAGS="-k ${CURL_FLAGS}"
fi

ha_get_state() {
    # shellcheck disable=SC2086
    curl ${CURL_FLAGS} \
      -H "Authorization: Bearer ${HA_TOKEN}" \
      -H "Content-Type: application/json" \
      "${HA_URL}/api/states/${HA_ENTITY_ID}" | grep -o '"state":"[^"]*"' | cut -d'"' -f4
}

ha_call_service() {
    local action="$1" # turn_on or turn_off
    # shellcheck disable=SC2086
    curl ${CURL_FLAGS} -X POST \
      -H "Authorization: Bearer ${HA_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"entity_id\": \"${HA_ENTITY_ID}\"}" \
      "${HA_URL}/api/services/switch/${action}" > /dev/null
}

echo "=== LUKS-COMPANION: TEST UNITARIO HOME ASSISTANT ==="
echo "[1/3] Lettura stato iniziale entità '${HA_ENTITY_ID}'..."
STATE=$(ha_get_state || echo "error")
if [ "$STATE" == "error" ]; then
    echo "[!] ERRORE: Impossibile contattare Home Assistant o Token errato." >&2
    echo "    Verifica HA_URL (${HA_URL}), HA_TOKEN ed HA_ENTITY_ID (${HA_ENTITY_ID})." >&2
    exit 1
fi
echo "[✓] Connessione riuscita! Stato attuale: [$STATE]"

echo -e "\n[2/3] Invio comando ACCENSIONE (turn_on)..."
ha_call_service "turn_on"
sleep 2
echo "[✓] Nuovo stato: [$(ha_get_state)]"

echo -e "\n-------------------------------------------------------------------"
read -p "Verifica l'accensione della presa! Premi [INVIO] per spegnere..."
echo "-------------------------------------------------------------------"

echo -e "\n[3/3] Invio comando SPEGNIMENTO (turn_off)..."
ha_call_service "turn_off"
sleep 2
echo "[✓] Stato finale: [$(ha_get_state)]"

echo -e "\n[✓] TEST UNITARIO COMPLETATO CON SUCCESSO!"

#!/usr/bin/env python3
# ===================================================================
# LUKS-COMPANION: Discord Webhook Notification Dispatcher
# Standalone Python module (Standard Library only, zero dependencies)
# ===================================================================
import os
import sys
import json
import socket
import datetime
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
ENV_FILE = os.path.join(REPO_DIR, ".env")

def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip('"').strip("'")
        except PermissionError:
            # If running unprivileged and .env is root 600, auto-escalate with sudo if in interactive terminal
            if sys.stdin.isatty() and hasattr(os, "geteuid") and os.geteuid() != 0:
                os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
    return env

def main():
    try:
        if REPO_DIR not in sys.path:
            sys.path.insert(0, REPO_DIR)
        from luks_companion.core.config import Config
        from luks_companion.core.notify import send_discord_notification

        cfg = Config.from_env_file(ENV_FILE)
        event = sys.argv[1] if len(sys.argv) > 1 else "test"
        custom_msg = sys.argv[2] if len(sys.argv) > 2 else ""

        if not cfg.discord_webhook_url or not cfg.discord_webhook_url.startswith("https://"):
            sys.exit(0)

        success = send_discord_notification(cfg, event, custom_msg)
        if sys.stdout.isatty():
            if success:
                print(f"[✓] Notifica Discord inviata con successo ({event})!")
            else:
                print(f"[✗] Errore invio notifica Discord ({event}).", file=sys.stderr)
        sys.exit(0 if success else 1)
    except Exception:
        pass

    env = load_env()
    webhook_url = env.get("DISCORD_WEBHOOK_URL", "").strip()

    # Security & Bypass check: If webhook URL is missing/empty, exit silently with zero overhead
    if not webhook_url or not webhook_url.startswith("https://"):
        sys.exit(0)

    event = sys.argv[1] if len(sys.argv) > 1 else "test"
    custom_msg = sys.argv[2] if len(sys.argv) > 2 else ""

    host_name = socket.gethostname()
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vg_name = env.get("VG_NAME", "N/D")
    mapper_name = env.get("MAPPER_NAME", "")
    storage_base = env.get("STORAGE_BASE", "/srv/storage")
    mount_crypto = env.get("MOUNT_CRYPTO") or (os.path.join(storage_base, mapper_name) if mapper_name else "N/D")
    enable_webdav = env.get("ENABLE_WEBDAV", "true").lower() == "true"
    webdav_port = env.get("WEBDAV_PORT", "9088")
    idle_timeout = env.get("IDLE_TIMEOUT_MIN", "30")

    webdav_val = f"Attivo (Porta {webdav_port})" if enable_webdav else "Inattivo"

    # Event Styling & Content Mapping
    color = 3718648  # Default Cyan (0x38BDF8)
    title = "ℹ️ Notifica LUKS Companion"
    desc = "Evento registrato sul sottosistema storage."

    if event in ["unlock", "start"]:
        color = 1096065  # Green (0x10B981)
        title = "🔓 Storage LUKS Sbloccato & Montato"
        desc = "Il container cifrato LUKS2 è stato sbloccato in RAM ed i volumi sono montati e pronti all'uso."
    elif event in ["lock", "stop"]:
        color = 15680580  # Red (0xEF4444)
        title = "🛑 Storage LUKS Sigillato & Spento"
        desc = "Filesystem smontati, chiave distrutta da RAM, testine SCSI parcheggiate ed alimentazione 220V interrotta (0W Standby)."
    elif event == "watchdog":
        color = 16096779  # Amber (0xF59E0B)
        title = "⏱️ Watchdog: Arresto Automatico per Inattività"
        desc = f"Nessuna attività I/O rilevata sul disco per {idle_timeout} minuti. Lo storage è stato espulso e spento in sicurezza (0W Standby)."
    elif event in ["error", "fail"]:
        color = 15680580  # Red (0xEF4444)
        title = "⚠️ Errore Storage LUKS"
        desc = custom_msg or "Si è verificato un errore durante un'operazione sul sottosistema storage."
    elif event == "test":
        color = 3718648  # Cyan (0x38BDF8)
        title = "🧪 Test Notifiche LUKS Companion"
        desc = "Integrazione webhook Discord verificata e funzionante con successo!"

    fields = [
        {"name": "🖥️ Host", "value": f"`{host_name}`", "inline": True},
        {"name": "📦 Volume Group", "value": f"`{vg_name}`", "inline": True},
    ]

    if event in ["unlock", "start"]:
        fields.append({"name": "📁 Punto di Mount", "value": f"`{mount_crypto}`", "inline": False})
        fields.append({"name": "🌐 WebDAV", "value": webdav_val, "inline": True})
    elif event in ["lock", "stop", "watchdog"]:
        fields.append({"name": "⚡ Alimentazione", "value": "0W Standby (Spento)", "inline": True})

    if custom_msg and event not in ["error", "fail"]:
        fields.append({"name": "📝 Dettagli", "value": custom_msg, "inline": False})

    payload = {
        "username": "LUKS Companion",
        "embeds": [
            {
                "title": title,
                "description": desc,
                "color": color,
                "fields": fields,
                "footer": {
                    "text": "LUKS Companion • Zero-Standby Storage Subsystem"
                },
                "timestamp": now_iso
            }
        ]
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LUKS-Companion-Notifier/1.0"
        },
        method="POST"
    )

    is_tty = sys.stdout.isatty()

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
            if is_tty:
                print(f"[✓] Notifica Discord inviata con successo ({event}, HTTP {code})!")
            sys.exit(0)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        if is_tty:
            print(f"[✗] Errore risposta Discord (HTTP {e.code}): {err_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if is_tty:
            print(f"[✗] Errore connessione Discord: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

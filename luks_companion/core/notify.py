"""
Discord Webhook Notification Dispatcher.
Dispatches rich embed notifications for unlock, lock, watchdog, and error events.
"""
import os
import sys
import json
import socket
import datetime
import urllib.request
import urllib.error
from typing import Optional


def send_discord_notification(config, event: str = "test", custom_msg: str = "") -> bool:
    webhook_url = getattr(config, "discord_webhook_url", "").strip()
    if not webhook_url or not webhook_url.startswith("https://"):
        return False

    host_name = socket.gethostname()
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vg_name = getattr(config, "vg_name", "N/D") or "N/D"
    mount_crypto = getattr(config, "mount_crypto", "N/D") or "N/D"
    enable_webdav = getattr(config, "enable_webdav", True)
    webdav_port = getattr(config, "webdav_port", 9088)
    idle_timeout = getattr(config, "idle_timeout_min", 30)

    webdav_val = f"Attivo (Porta {webdav_port})" if enable_webdav else "Inattivo"

    # Event Styling & Content Mapping
    color = 3718648  # Default Cyan (0x38BDF8)
    title = "↹️ Notifica LUKS Companion"
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
                "timestamp": now_iso,
            }
        ],
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LUKS-Companion-Notifier/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode() in (200, 204)
    except urllib.error.HTTPError as e:
        err_detail = ""
        try:
            err_detail = f" - {e.read().decode('utf-8', errors='replace')}"
        except Exception:
            pass
        print(f"[!] Invio notifica Discord ({event}) fallito (HTTP {e.code}{err_detail})", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[!] Invio notifica Discord ({event}) fallito: {e}", file=sys.stderr)
        return False

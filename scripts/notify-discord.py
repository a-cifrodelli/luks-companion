#!/usr/bin/env python3
# ===================================================================
# LUKS-COMPANION: Discord Webhook Notification Dispatcher
# Standalone Python module (Standard Library only, zero dependencies)
# ===================================================================
import os
import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)
ENV_FILE = os.path.join(REPO_DIR, ".env")

from luks_companion.core.config import Config
from luks_companion.core.notify import send_discord_notification


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "test"
    custom_msg = sys.argv[2] if len(sys.argv) > 2 else ""

    cfg = Config.from_env_file(ENV_FILE)

    if not cfg.discord_webhook_url:
        if sys.stderr.isatty():
            print("[!] DISCORD_WEBHOOK_URL non configurata in .env.", file=sys.stderr)
        sys.exit(0)

    success = send_discord_notification(cfg, event, custom_msg)
    if success:
        if sys.stdout.isatty():
            print(f"[✓] Notifica Discord inviata con successo ({event})!")
        sys.exit(0)
    else:
        if sys.stderr.isatty():
            print(f"[✗] Errore invio notifica Discord ({event}).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

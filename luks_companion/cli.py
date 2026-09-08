"""
Unified Command Line Interface for luks-companion.
Replaces luks-manager.sh with a typed, extensible, and user-friendly CLI.
"""
import sys
import os
import argparse
import getpass
import json
from typing import Optional

from .core.config import Config
from .core.runner import SubprocessRunner, StorageEngineError
from .core.engine import StorageEngine
from .core.diagnose import run_full_diagnosis, format_diagnosis_cli
from .daemon.server import run_daemon, SocketDaemon
from .web.app import run_web_server, send_socket_command


def check_root_privileges() -> bool:
    if hasattr(os, "getuid"):
        return os.getuid() == 0
    return True


def ensure_root_or_rerun():
    if hasattr(os, "getuid") and os.getuid() != 0:
        print("[!] Permessi di root necessari. Richiesta elevazione sudo...", file=sys.stderr)
        args = ["sudo", sys.executable, "-m", "luks_companion"] + sys.argv[1:]
        import subprocess
        ret = subprocess.call(args)
        sys.exit(ret)


def main():
    parser = argparse.ArgumentParser(
        prog="luks-companion",
        description="Unified Storage Encryption & Lifecycle Manager (LUKS2 / LVM / HA / WebDAV)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Comando da eseguire")

    # START / UNLOCK
    p_start = subparsers.add_parser("start", aliases=["unlock"], help="Accende la presa, rileva il disco, attiva LVM e monta i volumi cifrati")
    p_start.add_argument("--keyfile", "-k", type=str, help="Percorso del file chiave di sblocco")
    p_start.add_argument("--no-watchdog", "--daemon", action="store_true", help="Non avvia il watchdog interattivo in primo piano")

    # STOP / LOCK
    p_stop = subparsers.add_parser("stop", aliases=["lock"], help="Esegue il teardown atomico sicuro e spegne il disco")

    # STATUS
    p_status = subparsers.add_parser("status", help="Mostra lo stato corrente dello storage e della presa")
    p_status.add_argument("--json", action="store_true", help="Formatta l'output in JSON")

    # DIAGNOSE
    p_diag = subparsers.add_parser("diagnose", help="Check-up diagnostico completo per rilevare anomalie di sistema e hardware")
    p_diag.add_argument("--json", action="store_true", help="Formatta il report diagnostico in JSON")

    # BACKUP-HEADER
    p_bkhdr = subparsers.add_parser("backup-header", help="Esegue il backup dell'header LUKS2 in un file locale")
    p_bkhdr.add_argument("--out", "-o", type=str, help="Percorso file di destinazione")

    # RESTORE-HEADER
    p_rshdr = subparsers.add_parser("restore-header", help="Ripristina l'header LUKS2 da un file di backup")
    p_rshdr.add_argument("file", type=str, help="Percorso del file .header da ripristinare")

    # DAEMON (Root service)
    p_daemon = subparsers.add_parser("daemon", help="Avvia il demone socket IPC e il watchdog in background (gestito da systemd)")

    # WEB (Unprivileged service)
    p_web = subparsers.add_parser("web", help="Avvia il gateway web HTTP")
    p_web.add_argument("--drop-privileges", type=str, default="luks-web", help="Utente a cui cedere i privilegi di root")
    p_web.add_argument("--mock", "--demo", action="store_true", help="Avvia il web gateway in modalità Mock/Demo con telemetria simulata per screenshot")

    # NOTIFY
    p_notify = subparsers.add_parser("notify", help="Invia una notifica via Webhook Discord (test, unlock, lock, watchdog, error)")
    p_notify.add_argument("event", nargs="?", default="test", help="Tipo evento (test, unlock, lock, watchdog, error)")
    p_notify.add_argument("message", nargs="?", default="", help="Messaggio o dettaglio personalizzato")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    cfg = Config.from_env_file()

    # 1. DIAGNOSE
    if args.command == "diagnose":
        diag_report = run_full_diagnosis(cfg)
        if args.json:
            print(json.dumps(diag_report, indent=2))
        else:
            print(format_diagnosis_cli(diag_report))
        sys.exit(0)

    # 2. STATUS
    elif args.command == "status":
        # Check if daemon socket is active first
        if os.path.exists(cfg.socket_path):
            resp = send_socket_command(cfg.socket_path, {"action": "status"})
            data = resp.get("data", resp)
        else:
            engine = StorageEngine(cfg)
            data = engine.get_status()

        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"=== STATO STORAGE: {data.get('status', 'unknown').upper()} ===")
            print(f"  - Alimentazione Presa HA: {data.get('plug_state', 'unknown')}")
            print(f"  - Volume Group ({data.get('vg_name')}): {'ATTIVO' if data.get('vg_active') else 'NON ATTIVO'}")
            print(f"  - Container LUKS ({data.get('mapper_name')}): {'SBLOCCATO' if data.get('unlocked') else 'BLOCCATO'}")
            print(f"  - Filesystem Crypto ({data.get('mount_crypto')}): {'MONTATO' if data.get('mounted') else 'NON MONTATO'}")
            if data.get("mount_backup"):
                print(f"  - Filesystem Backup ({data.get('mount_backup')}): {'MONTATO' if data.get('mounted') else 'NON MONTATO'}")
            print(f"  - Servizio WebDAV: {'ATTIVO' if data.get('webdav_active') else 'NON ATTIVO'}")

            smart = data.get("smart", {})
            if smart.get("supported"):
                print(f"  - S.M.A.R.T.: {smart.get('health')} (Temp: {smart.get('temperature_c')}°C) - {smart.get('model')}")
            else:
                print(f"  - S.M.A.R.T.: {smart.get('reason', 'Non attivo')}")
        sys.exit(0)

    # 3. DAEMON
    elif args.command == "daemon":
        ensure_root_or_rerun()
        run_daemon()

    # 4. WEB
    elif args.command == "web":
        run_web_server(cfg, drop_privs_user=args.drop_privileges, mock=getattr(args, "mock", False))

    # 5. START / UNLOCK
    elif args.command in ("start", "unlock"):
        ensure_root_or_rerun()
        engine = StorageEngine(cfg)

        keyfile_path = args.keyfile
        passphrase = None
        key_bytes = None

        if not keyfile_path:
            # Check if stdin has data
            if not sys.stdin.isatty():
                stdin_data = sys.stdin.buffer.read()
                if stdin_data:
                    key_bytes = stdin_data
            else:
                passphrase = getpass.getpass("Inserisci Passphrase di sblocco LUKS2: ")

        try:
            engine.start(
                passphrase=passphrase,
                keyfile_bytes=key_bytes,
                keyfile_path=keyfile_path,
            )
            print("\n[✓] Storage sbloccato e operativo!")

            if not args.no_watchdog:
                print("[*] Modalità CLI interattiva: premi INVIO per arrestare lo storage in sicurezza...")
                try:
                    input()
                except (KeyboardInterrupt, EOFError):
                    print("")
                engine.stop()
        except StorageEngineError as see:
            print(f"\n{see.format_detailed()}", file=sys.stderr)
            sys.exit(1)
        except Exception as ex:
            print(f"\n[!] Errore critico: {ex}", file=sys.stderr)
            sys.exit(1)

    # 6. STOP / LOCK
    elif args.command in ("stop", "lock"):
        ensure_root_or_rerun()
        engine = StorageEngine(cfg)
        engine.stop()
        print("\n[✓] Procedura di teardown completata.")

    # 7. BACKUP-HEADER
    elif args.command == "backup-header":
        ensure_root_or_rerun()
        engine = StorageEngine(cfg)
        res = engine.backup_luks_header()
        import base64
        hdr_data = base64.b64decode(res["header_base64"])
        out_path = args.out or res["filename"]
        with open(out_path, "wb") as f:
            f.write(hdr_data)
        print(f"[✓] Backup header salvato con successo: {out_path} ({len(hdr_data)} bytes)")

    # 8. RESTORE-HEADER
    elif args.command == "restore-header":
        ensure_root_or_rerun()
        if not os.path.exists(args.file):
            print(f"[!] File non trovato: {args.file}", file=sys.stderr)
            sys.exit(1)

        print(f"[!] ATTENZIONE: Il ripristino dell'header sovrascriverà i metadati crittografici.")
        confirm = input("Digitare 'CONFERMA' per procedere: ")
        if confirm != "CONFERMA":
            print("Operazione annullata.")
            sys.exit(1)

        with open(args.file, "rb") as f:
            hdr_bytes = f.read()

        engine = StorageEngine(cfg)
        engine.restore_luks_header(hdr_bytes)
        print("[✓] Header LUKS2 ripristinato con successo!")

    # 9. NOTIFY
    elif args.command == "notify":
        from .core.notify import send_discord_notification
        print(f"[*] Invio notifica Discord ('{args.event}')...")
        success = send_discord_notification(cfg, args.event, args.message)
        if success:
            print(f"[✓] Notifica '{args.event}' inviata con successo su Discord!")
        else:
            if not cfg.discord_webhook_url:
                print("[!] DISCORD_WEBHOOK_URL non configurata in .env.", file=sys.stderr)
            else:
                print("[✗] Invio notifica fallito. Verificare la validità del Webhook URL in .env.", file=sys.stderr)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

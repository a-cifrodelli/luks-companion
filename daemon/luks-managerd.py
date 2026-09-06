#!/usr/bin/env python3
# ===================================================================
# LUKS-MANAGER DAEMON: UNIX Domain Socket API Server for WebApp
# ===================================================================
import os
import sys
import json
import socket
import subprocess
import signal

SOCKET_PATH = "/run/luks-manager.sock"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
MANAGER_SCRIPT = os.path.join(BASE_DIR, "luks-manager.sh")

def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip().strip('"').strip("'")
    return env

def get_status():
    env = load_env()
    mapper_name = env.get("MAPPER_NAME", "")
    mount_crypto = env.get("MOUNT_CRYPTO", "")
    vg_name = env.get("VG_NAME", "")

    is_unlocked = os.path.exists(f"/dev/mapper/{mapper_name}") if mapper_name else False
    is_mounted = os.path.ismount(mount_crypto) if mount_crypto else False
    webdav_active = subprocess.call(["systemctl", "is-active", "--quiet", "webdav"]) == 0

    return {
        "status": "mounted" if is_mounted else ("unlocked" if is_unlocked else "stopped"),
        "unlocked": is_unlocked,
        "mounted": is_mounted,
        "webdav_active": webdav_active,
        "vg_name": vg_name,
        "mapper_name": mapper_name,
        "mount_crypto": mount_crypto
    }

def send_response(conn, payload):
    try:
        data = json.dumps(payload).encode('utf-8')
        conn.sendall(data)
    except (BrokenPipeError, ConnectionResetError, OSError):
        # Client disconnected or closed socket before reading response
        pass

def handle_client(conn):
    try:
        data = conn.recv(4096)
        if not data:
            return
        
        try:
            req = json.loads(data.decode('utf-8'))
        except json.JSONDecodeError:
            send_response(conn, {"status": "error", "message": "Richiesta JSON non valida"})
            return

        action = req.get("action", "")

        if action == "status":
            send_response(conn, {"status": "ok", "data": get_status()})

        elif action == "unlock":
            passphrase = req.get("passphrase", "")
            if not passphrase:
                send_response(conn, {"status": "error", "message": "Passphrase vuota o mancante"})
                return

            proc = subprocess.Popen(
                ["/usr/bin/env", "bash", MANAGER_SCRIPT, "unlock", "--no-watchdog"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = proc.communicate(input=passphrase + "\n")
            
            if proc.returncode == 0:
                send_response(conn, {
                    "status": "ok",
                    "message": "Volume sbloccato e montato con successo",
                    "output": stdout.strip(),
                    "data": get_status()
                })
            else:
                send_response(conn, {
                    "status": "error",
                    "message": stderr.strip() or stdout.strip() or "Errore durante lo sblocco",
                    "data": get_status()
                })

        elif action == "stop":
            proc = subprocess.Popen(
                ["/usr/bin/env", "bash", MANAGER_SCRIPT, "stop"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = proc.communicate()
            
            if proc.returncode == 0:
                send_response(conn, {
                    "status": "ok",
                    "message": "Procedura di teardown e spegnimento completata",
                    "output": stdout.strip(),
                    "data": get_status()
                })
            else:
                send_response(conn, {
                    "status": "error",
                    "message": stderr.strip() or stdout.strip() or "Errore durante l'arresto",
                    "data": get_status()
                })

        else:
            send_response(conn, {"status": "error", "message": f"Azione '{action}' sconosciuta"})

    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:
        send_response(conn, {"status": "error", "message": str(e)})
    finally:
        try:
            conn.close()
        except OSError:
            pass

def main():
    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)
    server.listen(10)

    def cleanup(signum, frame):
        server.close()
        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"[*] LUKS Manager Socket Daemon attivo su: {SOCKET_PATH}")
    while True:
        try:
            conn, _ = server.accept()
            handle_client(conn)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[!] Errore connessione client: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()

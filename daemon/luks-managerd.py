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
import base64
import shutil
import threading

SOCKET_PATH = "/run/luks-manager.sock"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
MANAGER_SCRIPT = os.path.join(BASE_DIR, "luks-manager.sh")

action_lock = threading.Lock()

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

def format_bytes(bytes_num):
    if bytes_num is None or bytes_num < 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if bytes_num < 1024.0 or unit == 'PB':
            return f"{bytes_num:.1f} {unit}" if unit in ['GB', 'TB', 'PB'] else f"{int(bytes_num)} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"

def get_volume_stats(path, name):
    if not path or not os.path.ismount(path):
        return None
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        pct = round((used / total * 100), 1) if total > 0 else 0
        return {
            "name": name,
            "mountpoint": path,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": pct,
            "total_human": format_bytes(total),
            "used_human": format_bytes(used),
            "free_human": format_bytes(free),
        }
    except Exception:
        return None

def find_smartctl_bin():
    found = shutil.which("smartctl")
    if found:
        return found
    for cand in ["/usr/sbin/smartctl", "/usr/bin/smartctl", "/sbin/smartctl", "/bin/smartctl"]:
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None

def find_target_block_device(vg_name, mapper_name, mount_crypto, target_dev_cfg):
    if target_dev_cfg and os.path.exists(target_dev_cfg):
        return target_dev_cfg

    # Priority 1: Trace back from mapper, mountpoint, or VG device node using lsblk -s
    candidates_to_trace = []
    if mapper_name and os.path.exists(f"/dev/mapper/{mapper_name}"):
        candidates_to_trace.append(f"/dev/mapper/{mapper_name}")
    if mount_crypto and os.path.exists(mount_crypto):
        candidates_to_trace.append(mount_crypto)
    if vg_name and os.path.exists(f"/dev/{vg_name}"):
        candidates_to_trace.append(f"/dev/{vg_name}")

    for cand in candidates_to_trace:
        try:
            out = subprocess.check_output(
                ["lsblk", "-s", "-rno", "PATH,TYPE", cand],
                stderr=subprocess.DEVNULL, text=True, timeout=2
            ).strip()
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].lower() == "disk" and os.path.exists(parts[0]):
                    return parts[0]
        except Exception:
            pass

    # Priority 2: Query LVM physical volumes directly
    try:
        out = subprocess.check_output(
            ["pvs", "--noheadings", "-o", "pv_name"],
            stderr=subprocess.DEVNULL, text=True, timeout=2
        ).strip()
        for pv in out.splitlines():
            pv = pv.strip()
            if pv and os.path.exists(pv):
                try:
                    pout = subprocess.check_output(
                        ["lsblk", "-s", "-rno", "PATH,TYPE", pv],
                        stderr=subprocess.DEVNULL, text=True, timeout=2
                    ).strip()
                    for line in pout.splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[1].lower() == "disk" and os.path.exists(parts[0]):
                            return parts[0]
                except Exception:
                    pass
                return pv
    except Exception:
        pass

    return None

def get_smart_data(target_dev):
    smartctl = find_smartctl_bin()
    if not smartctl:
        return {"supported": False, "installed": False, "reason": "smartctl non installato (sudo pacman -S smartmontools)"}
    if not target_dev or not os.path.exists(target_dev):
        return {"supported": False, "installed": True, "device": None, "reason": "Disco spento / inerte (0W Standby)"}

    # Try standard probe first, then SAT (SCSI to ATA Translation) fallback
    raw_data = None
    for cmd in [
        [smartctl, "-j", "-i", "-H", "-A", target_dev],
        [smartctl, "-d", "sat", "-j", "-i", "-H", "-A", target_dev],
        [smartctl, "-d", "auto", "-j", "-i", "-H", "-A", target_dev]
    ]:
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3)
            if res.stdout and ("smart_status" in res.stdout or "device" in res.stdout or "model_name" in res.stdout):
                raw_data = json.loads(res.stdout)
                break
        except Exception:
            continue

    if not raw_data:
        return {"supported": False, "installed": True, "device": target_dev, "reason": "S.M.A.R.T. non esposto dal bridge USB"}

    health = "UNKNOWN"
    smart_status = raw_data.get("smart_status", {})
    if "passed" in smart_status:
        health = "PASSED" if smart_status["passed"] else "FAILED"

    temp = None
    if "temperature" in raw_data and "current" in raw_data["temperature"]:
        temp = raw_data["temperature"]["current"]
    elif "ata_smart_attributes" in raw_data and "table" in raw_data["ata_smart_attributes"]:
        for attr in raw_data["ata_smart_attributes"]["table"]:
            if attr.get("name") in ["Temperature_Celsius", "Airflow_Temperature_Cel", "Temperature"]:
                temp = attr.get("raw", {}).get("value")
                break
    elif "nvme_smart_health_information_log" in raw_data and "temperature" in raw_data["nvme_smart_health_information_log"]:
        temp = raw_data["nvme_smart_health_information_log"]["temperature"]

    model = raw_data.get("model_name") or raw_data.get("device", {}).get("model_name") or raw_data.get("model_family") or "USB Drive"
    serial = raw_data.get("serial_number", "")

    return {
        "supported": True,
        "installed": True,
        "device": target_dev,
        "health": health,
        "temperature_c": temp,
        "model": model,
        "serial": serial
    }

def get_status():
    env = load_env()
    mapper_name = env.get("MAPPER_NAME", "")
    vg_name = env.get("VG_NAME", "")
    lv_crypto = env.get("LV_CRYPTO", "")
    lv_backup = env.get("LV_BACKUP", "")
    storage_base = env.get("STORAGE_BASE", "/srv/storage")
    target_dev_cfg = env.get("TARGET_DEV", "")

    # Mount points calculated dynamically under STORAGE_BASE
    mount_crypto = os.path.join(storage_base, mapper_name) if mapper_name else ""
    mount_backup = os.path.join(storage_base, lv_backup) if lv_backup else ""

    # Check physical block device presence on USB/SCSI bus
    target_dev = find_target_block_device(vg_name, mapper_name, mount_crypto, target_dev_cfg)
    disk_present = target_dev is not None and os.path.exists(target_dev)

    is_mounted = os.path.ismount(mount_crypto) if mount_crypto else False
    is_unlocked = (os.path.exists(f"/dev/mapper/{mapper_name}") and disk_present) if mapper_name else False

    vg_active = False
    if disk_present and vg_name:
        vg_dev = f"/dev/{vg_name}"
        mapper_lv = f"/dev/mapper/{vg_name}-{lv_crypto}" if lv_crypto else ""
        vg_active = (os.path.exists(vg_dev) and os.path.isdir(vg_dev)) or (mapper_lv and os.path.exists(mapper_lv))

    webdav_active = subprocess.call(["systemctl", "is-active", "--quiet", "webdav"]) == 0
    webdav_port = env.get("WEBDAV_PORT", "")

    # Volume Storage Statistics
    volumes = []
    if is_mounted:
        v_crypto = get_volume_stats(mount_crypto, "Dati Cifrati (crypto_data)")
        if v_crypto:
            volumes.append(v_crypto)
        
        if mount_backup:
            v_backup = get_volume_stats(mount_backup, "Backup (backup_data)")
            if v_backup:
                volumes.append(v_backup)

    # S.M.A.R.T. Telemetry
    smart_info = get_smart_data(target_dev if disk_present else None)

    return {
        "status": "mounted" if is_mounted else ("unlocked" if is_unlocked else "stopped"),
        "unlocked": is_unlocked,
        "mounted": is_mounted,
        "vg_active": vg_active,
        "webdav_active": webdav_active,
        "webdav_port": webdav_port,
        "vg_name": vg_name,
        "mapper_name": mapper_name,
        "mount_crypto": mount_crypto,
        "volumes": volumes,
        "smart": smart_info,
        "smartctl_installed": find_smartctl_bin() is not None,
        "busy": action_lock.locked()
    }

def send_response(conn, payload):
    try:
        data = json.dumps(payload).encode('utf-8')
        conn.sendall(data)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass

def handle_client(conn):
    try:
        data = conn.recv(65536)
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
            if not action_lock.acquire(blocking=False):
                send_response(conn, {"status": "busy", "message": "Un'altra operazione è già in corso sul disco..."})
                return

            try:
                key_payload = None
                
                # 1. Base64 encoded binary keyfile (from keyfile tab or stego image)
                if req.get("keyfile_base64"):
                    try:
                        key_payload = base64.b64decode(req["keyfile_base64"])
                    except Exception as e:
                        send_response(conn, {"status": "error", "message": f"Decodifica keyfile base64 fallita: {e}"})
                        return

                # 2. Plain passphrase string (from passphrase tab) - NO trailing newline!
                elif req.get("passphrase"):
                    key_payload = req["passphrase"].encode('utf-8')

                if not key_payload:
                    send_response(conn, {"status": "error", "message": "Nessuna passphrase o keyfile fornito"})
                    return

                proc = subprocess.Popen(
                    ["/usr/bin/env", "bash", MANAGER_SCRIPT, "unlock", "--no-watchdog"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout_bytes, stderr_bytes = proc.communicate(input=key_payload)
                
                # Wipe key payload from RAM
                del key_payload

                stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
                stderr = stderr_bytes.decode('utf-8', errors='replace').strip()
                
                if proc.returncode == 0:
                    send_response(conn, {
                        "status": "ok",
                        "message": "Volume sbloccato e montato con successo",
                        "output": stdout,
                        "data": get_status()
                    })
                else:
                    send_response(conn, {
                        "status": "error",
                        "message": stderr or stdout or "Errore durante lo sblocco",
                        "data": get_status()
                    })
            finally:
                action_lock.release()

        elif action == "stop":
            if not action_lock.acquire(blocking=False):
                send_response(conn, {"status": "busy", "message": "Operazione di arresto già in corso..."})
                return

            try:
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
            finally:
                action_lock.release()

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
    server.listen(15)

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
            threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[!] Errore connessione client: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()

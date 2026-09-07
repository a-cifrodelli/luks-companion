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
import time
import datetime
import shutil
import threading
import urllib.request
import urllib.error

SOCKET_PATH = "/run/luks-manager.sock"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
MANAGER_SCRIPT = os.path.join(BASE_DIR, "luks-manager.sh")
NOTIFY_SCRIPT = os.path.join(BASE_DIR, "scripts", "notify-discord.py")

action_lock = threading.Lock()

_last_ha_check_time = 0
_last_ha_state = "unknown"

def get_ha_plug_state(env):
    global _last_ha_check_time, _last_ha_state
    if env.get("ENABLE_HOME_ASSISTANT") != "true":
        return "always-on"
    
    ha_url = env.get("HA_URL")
    ha_token = env.get("HA_TOKEN")
    ha_entity_id = env.get("HA_ENTITY_ID")
    if not ha_url or not ha_token or not ha_entity_id:
        return "unknown"
    
    now = time.time()
    if now - _last_ha_check_time < 2.0 and _last_ha_state != "unknown":
        return _last_ha_state
        
    try:
        url = f"{ha_url.rstrip('/')}/api/states/{ha_entity_id}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            state = data.get("state", "unknown").lower()
            _last_ha_state = state
            _last_ha_check_time = now
            return state
    except Exception:
        return _last_ha_state if (now - _last_ha_check_time < 10.0) else "unknown"

def notify_discord(event, message=""):
    if os.path.exists(NOTIFY_SCRIPT):
        try:
            subprocess.Popen([sys.executable, NOTIFY_SCRIPT, event, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

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

def is_system_disk(dev_path):
    if not dev_path or not os.path.exists(dev_path):
        return False
    try:
        real_target = os.path.realpath(dev_path)
        target_name = os.path.basename(real_target)
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "/", "/boot", "/boot/firmware"],
            stderr=subprocess.DEVNULL, text=True, timeout=2
        ).strip()
        for src in out.splitlines():
            src = src.strip()
            if not src:
                continue
            src_real = os.path.realpath(src)
            if real_target == src_real:
                return True
            try:
                parent = subprocess.check_output(
                    ["lsblk", "-no", "PKNAME", src_real],
                    stderr=subprocess.DEVNULL, text=True, timeout=1
                ).strip()
                if parent and (f"/dev/{parent}" == real_target or target_name == parent):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False

def find_target_block_device(vg_name, mapper_name, mount_crypto, target_dev_cfg):
    if target_dev_cfg and os.path.exists(target_dev_cfg) and not is_system_disk(target_dev_cfg):
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
                    if not is_system_disk(parts[0]):
                        return parts[0]
        except Exception:
            pass

    # Priority 2: Query LVM physical volumes directly (matching VG name if provided)
    try:
        cmd = ["pvs", "--noheadings", "-o", "pv_name,vg_name"]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
        for line in out.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            pv = parts[0]
            pv_vg = parts[1] if len(parts) > 1 else ""
            if vg_name and pv_vg and pv_vg != vg_name:
                continue
            if pv and os.path.exists(pv) and not is_system_disk(pv):
                try:
                    pout = subprocess.check_output(
                        ["lsblk", "-s", "-rno", "PATH,TYPE", pv],
                        stderr=subprocess.DEVNULL, text=True, timeout=2
                    ).strip()
                    for pline in pout.splitlines():
                        pparts = pline.strip().split()
                        if len(pparts) >= 2 and pparts[1].lower() == "disk" and os.path.exists(pparts[0]):
                            if not is_system_disk(pparts[0]):
                                return pparts[0]
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
    if not target_dev or not os.path.exists(target_dev) or is_system_disk(target_dev):
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

    # Check physical block device presence on USB/SCSI bus (strictly ignoring OS system disk)
    target_dev = find_target_block_device(vg_name, mapper_name, mount_crypto, target_dev_cfg)
    disk_present = target_dev is not None and os.path.exists(target_dev) and not is_system_disk(target_dev)

    is_mounted = os.path.ismount(mount_crypto) if mount_crypto else False
    is_unlocked = (os.path.exists(f"/dev/mapper/{mapper_name}") and disk_present) if mapper_name else False

    vg_active = False
    if disk_present and vg_name:
        vg_dev = f"/dev/{vg_name}"
        mapper_lv = f"/dev/mapper/{vg_name}-{lv_crypto}" if lv_crypto else ""
        vg_active = (os.path.exists(vg_dev) and os.path.isdir(vg_dev)) or (mapper_lv and os.path.exists(mapper_lv))

    webdav_active = subprocess.call(["systemctl", "is-active", "--quiet", "webdav"]) == 0
    webdav_port = env.get("WEBDAV_PORT", "")

    # Check Home Assistant smart plug status
    ha_state = get_ha_plug_state(env)
    if env.get("ENABLE_HOME_ASSISTANT") == "true":
        plug_powered = (ha_state == "on")
    else:
        plug_powered = disk_present

    # Multi-state detection
    if is_mounted:
        master_status = "mounted"
    elif is_unlocked:
        master_status = "unlocked"
    elif plug_powered:
        master_status = "standby"
    else:
        master_status = "stopped"

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
        "status": master_status,
        "unlocked": is_unlocked,
        "mounted": is_mounted,
        "vg_active": vg_active,
        "plug_state": ha_state,
        "plug_powered": plug_powered,
        "disk_present": disk_present,
        "target_dev": target_dev,
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
        data = (json.dumps(payload) + "\n").encode('utf-8')
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
            send_response(conn, {"event": "done", "status": "error", "message": "Richiesta JSON non valida"})
            return

        action = req.get("action", "")

        if action == "status":
            send_response(conn, {"event": "done", "status": "ok", "data": get_status()})

        elif action == "unlock":
            if not action_lock.acquire(blocking=False):
                send_response(conn, {"event": "done", "status": "busy", "message": "Un'altra operazione è già in corso sul disco..."})
                return

            try:
                key_payload = None
                
                # 1. Base64 encoded binary keyfile (from keyfile tab or stego image)
                if req.get("keyfile_base64"):
                    try:
                        key_payload = base64.b64decode(req["keyfile_base64"])
                    except Exception as e:
                        send_response(conn, {"event": "done", "status": "error", "message": f"Decodifica keyfile base64 fallita: {e}"})
                        return

                # 2. Plain passphrase string (from passphrase tab) - NO trailing newline!
                elif req.get("passphrase"):
                    key_payload = req["passphrase"].encode('utf-8')

                if not key_payload:
                    err_msg = "Nessuna passphrase o keyfile fornito"
                    notify_discord("error", err_msg)
                    send_response(conn, {"event": "done", "status": "error", "message": err_msg})
                    return

                proc = subprocess.Popen(
                    ["/usr/bin/env", "bash", MANAGER_SCRIPT, "unlock", "--no-watchdog"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )

                # Feed stdin in background thread
                def write_stdin():
                    try:
                        proc.stdin.write(key_payload)
                        proc.stdin.flush()
                        proc.stdin.close()
                    except Exception:
                        pass

                threading.Thread(target=write_stdin, daemon=True).start()

                full_output = []
                for raw_line in iter(proc.stdout.readline, b''):
                    line_str = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
                    if line_str:
                        full_output.append(line_str)
                        send_response(conn, {
                            "event": "log",
                            "line": line_str,
                            "data": get_status()
                        })

                proc.wait()
                del key_payload

                if proc.returncode == 0:
                    send_response(conn, {
                        "event": "done",
                        "status": "ok",
                        "message": "Volume sbloccato e montato con successo",
                        "output": "\n".join(full_output),
                        "data": get_status()
                    })
                else:
                    err_msg = full_output[-1] if full_output else "Errore durante lo sblocco"
                    notify_discord("error", err_msg)
                    send_response(conn, {
                        "event": "done",
                        "status": "error",
                        "message": err_msg,
                        "output": "\n".join(full_output),
                        "data": get_status()
                    })
            finally:
                action_lock.release()

        elif action == "stop":
            if not action_lock.acquire(blocking=False):
                send_response(conn, {"event": "done", "status": "busy", "message": "Operazione di arresto già in corso..."})
                return

            try:
                proc = subprocess.Popen(
                    ["/usr/bin/env", "bash", MANAGER_SCRIPT, "stop"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
                full_output = []
                for raw_line in iter(proc.stdout.readline, b''):
                    line_str = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
                    if line_str:
                        full_output.append(line_str)
                        send_response(conn, {
                            "event": "log",
                            "line": line_str,
                            "data": get_status()
                        })

                proc.wait()

                if proc.returncode == 0:
                    send_response(conn, {
                        "event": "done",
                        "status": "ok",
                        "message": "Procedura di teardown e spegnimento completata",
                        "output": "\n".join(full_output),
                        "data": get_status()
                    })
                else:
                    err_msg = full_output[-1] if full_output else "Errore durante l'arresto"
                    notify_discord("error", err_msg)
                    send_response(conn, {
                        "event": "done",
                        "status": "error",
                        "message": err_msg,
                        "output": "\n".join(full_output),
                        "data": get_status()
                    })
            finally:
                action_lock.release()

        elif action == "header_backup":
            env = load_env()
            vg_name = env.get("VG_NAME", "")
            lv_crypto = env.get("LV_CRYPTO", "")
            if not vg_name or not lv_crypto:
                send_response(conn, {"event": "done", "status": "error", "message": "VG_NAME o LV_CRYPTO non configurati nel file .env"})
                return

            lv_path = f"/dev/{vg_name}/{lv_crypto}"
            
            # Activate LVM VG if not yet activated
            if not os.path.exists(lv_path):
                subprocess.call(["vgscan", "--mknodes"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.call(["vgchange", "-ay", vg_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if not os.path.exists(lv_path):
                send_response(conn, {"event": "done", "status": "error", "message": f"Dispositivo {lv_path} non trovato. Verificare che il disco sia alimentato e connesso."})
                return

            temp_header = "/dev/shm/luks_header_backup.bin"
            try:
                res = subprocess.run(
                    ["cryptsetup", "luksHeaderBackup", lv_path, "--header-backup-file", temp_header],
                    capture_output=True,
                    text=True
                )
                if res.returncode != 0:
                    send_response(conn, {"event": "done", "status": "error", "message": f"Errore cryptsetup: {res.stderr.strip()}"})
                    return

                with open(temp_header, "rb") as f:
                    header_bytes = f.read()

                header_b64 = base64.b64encode(header_bytes).decode('ascii')
                date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"luks_header_{vg_name}_{lv_crypto}_{date_str}.header"

                send_response(conn, {
                    "event": "done",
                    "status": "ok",
                    "header_base64": header_b64,
                    "filename": filename,
                    "size_bytes": len(header_bytes)
                })
            finally:
                if os.path.exists(temp_header):
                    try:
                        subprocess.call(["shred", "-u", temp_header], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        try:
                            os.remove(temp_header)
                        except OSError:
                            pass

        elif action == "header_restore":
            if not action_lock.acquire(blocking=False):
                send_response(conn, {"event": "done", "status": "busy", "message": "Un'altra operazione è già in corso sul disco..."})
                return

            try:
                env = load_env()
                vg_name = env.get("VG_NAME", "")
                lv_crypto = env.get("LV_CRYPTO", "")
                mapper_name = env.get("MAPPER_NAME", "")
                storage_base = env.get("STORAGE_BASE", "/srv/storage")
                mount_crypto = os.path.join(storage_base, mapper_name) if mapper_name else ""

                # Safety check: Cannot restore header while volume is mounted or mapper is open
                if (mount_crypto and os.path.ismount(mount_crypto)) or (mapper_name and os.path.exists(f"/dev/mapper/{mapper_name}")):
                    send_response(conn, {
                        "event": "done",
                        "status": "error",
                        "message": "Impossibile ripristinare l'header mentre lo storage è aperto o montato. Arrestare prima il volume!"
                    })
                    return

                header_b64 = req.get("header_base64", "")
                if not header_b64:
                    send_response(conn, {"event": "done", "status": "error", "message": "Nessun file header caricato per il ripristino"})
                    return

                try:
                    header_bytes = base64.b64decode(header_b64)
                except Exception as e:
                    send_response(conn, {"event": "done", "status": "error", "message": f"Dati header non validi: {e}"})
                    return

                lv_path = f"/dev/{vg_name}/{lv_crypto}"
                if not os.path.exists(lv_path):
                    subprocess.call(["vgscan", "--mknodes"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.call(["vgchange", "-ay", vg_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                if not os.path.exists(lv_path):
                    send_response(conn, {"event": "done", "status": "error", "message": f"Dispositivo {lv_path} non trovato. Verificare che il disco sia connesso."})
                    return

                temp_restore = "/dev/shm/luks_header_restore.bin"
                try:
                    with open(temp_restore, "wb") as f:
                        f.write(header_bytes)
                    os.chmod(temp_restore, 0o600)

                    res = subprocess.run(
                        ["cryptsetup", "luksHeaderRestore", lv_path, "--header-backup-file", temp_restore, "--batch-mode"],
                        capture_output=True,
                        text=True
                    )
                    if res.returncode == 0:
                        send_response(conn, {
                            "event": "done",
                            "status": "ok",
                            "message": "Header LUKS ripristinato con successo sul volume!",
                            "output": res.stdout.strip()
                        })
                    else:
                        send_response(conn, {
                            "event": "done",
                            "status": "error",
                            "message": f"Errore cryptsetup durante il ripristino: {res.stderr.strip()}"
                        })
                finally:
                    if os.path.exists(temp_restore):
                        try:
                            subprocess.call(["shred", "-u", temp_restore], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            try:
                                os.remove(temp_restore)
                            except OSError:
                                pass
            finally:
                action_lock.release()

        else:
            send_response(conn, {"event": "done", "status": "error", "message": f"Azione '{action}' sconosciuta"})

    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:
        send_response(conn, {"event": "done", "status": "error", "message": str(e)})
    finally:
        try:
            conn.close()
        except OSError:
            pass

def get_io_stats_for_device(dev_path):
    if not dev_path or not os.path.exists(dev_path):
        return None
    try:
        dev_real = os.path.realpath(dev_path)
        dev_name = os.path.basename(dev_real)
        for stat_path in [f"/sys/class/block/{dev_name}/stat", f"/sys/block/{dev_name}/stat"]:
            if os.path.isfile(stat_path):
                with open(stat_path, "r") as f:
                    parts = f.read().split()
                    if len(parts) >= 5:
                        return f"{parts[0]}:{parts[4]}"
    except Exception:
        pass
    return None

def idle_watchdog_loop():
    check_interval = 15
    idle_seconds = 0
    last_stats = None

    while True:
        time.sleep(check_interval)
        try:
            env = load_env()
            try:
                timeout_min = int(env.get("IDLE_TIMEOUT_MIN", "30"))
            except ValueError:
                timeout_min = 30

            if timeout_min <= 0:
                idle_seconds = 0
                last_stats = None
                continue

            max_idle_seconds = timeout_min * 60

            vg_name = env.get("VG_NAME", "")
            mapper_name = env.get("MAPPER_NAME", "")
            storage_base = env.get("STORAGE_BASE", "/srv/storage")
            target_dev_cfg = env.get("TARGET_DEV", "")
            mount_crypto = os.path.join(storage_base, mapper_name) if mapper_name else ""

            if not mount_crypto or not os.path.ismount(mount_crypto):
                idle_seconds = 0
                last_stats = None
                continue

            target_dev = find_target_block_device(vg_name, mapper_name, mount_crypto, target_dev_cfg)
            if not target_dev:
                continue

            current_stats = get_io_stats_for_device(target_dev)
            if current_stats is None:
                continue

            if last_stats is None:
                last_stats = current_stats
                idle_seconds = 0
                continue

            if current_stats == last_stats:
                idle_seconds += check_interval
                if idle_seconds >= max_idle_seconds:
                    print(f"[*] WATCHDOG: Inattività I/O rilevata per {idle_seconds // 60} min ({timeout_min}m max). Avvio spegnimento automatico...")
                    if action_lock.acquire(blocking=False):
                        try:
                            proc = subprocess.Popen(
                                ["/usr/bin/env", "bash", MANAGER_SCRIPT, "stop"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
                            )
                            proc.communicate()
                            notify_discord("watchdog")
                        finally:
                            action_lock.release()
                        idle_seconds = 0
                        last_stats = None
            else:
                idle_seconds = 0
                last_stats = current_stats
        except Exception as e:
            print(f"[!] Errore nel watchdog daemon: {e}", file=sys.stderr)

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

    # Start background Idle Watchdog thread
    threading.Thread(target=idle_watchdog_loop, daemon=True).start()

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

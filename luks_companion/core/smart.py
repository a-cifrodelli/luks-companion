"""
S.M.A.R.T. Hardware Telemetry & Volume Storage Statistics.
"""
import os
import shutil
import json
from typing import Optional, Dict, Any, List
from .runner import SystemRunner, SubprocessRunner


def format_bytes(bytes_num: Optional[float]) -> str:
    if bytes_num is None or bytes_num < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if bytes_num < 1024.0 or unit == "PB":
            return f"{bytes_num:.1f} {unit}" if unit in ["GB", "TB", "PB"] else f"{int(bytes_num)} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


def get_volume_stats(path: str, name: str, runner: Optional[SystemRunner] = None) -> Optional[Dict[str, Any]]:
    runner = runner or SubprocessRunner()
    if not path or not runner.is_mountpoint(path):
        return None
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        pct = round((used / total * 100), 1) if total > 0 else 0.0
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


def find_smartctl_bin() -> Optional[str]:
    found = shutil.which("smartctl")
    if found:
        return found
    for cand in ["/usr/sbin/smartctl", "/usr/bin/smartctl", "/sbin/smartctl", "/bin/smartctl"]:
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def get_smart_telemetry(target_dev: Optional[str], runner: Optional[SystemRunner] = None) -> Dict[str, Any]:
    if not target_dev:
        return {
            "supported": False,
            "installed": True,
            "device": None,
            "reason": "Disco spento / inerte (0W Standby)",
        }

    smartctl = find_smartctl_bin()
    if not smartctl:
        return {
            "supported": False,
            "installed": False,
            "reason": "smartctl non installato (installare pacchetto smartmontools)",
        }

    runner = runner or SubprocessRunner()
    if not runner.path_exists(target_dev):
        return {
            "supported": False,
            "installed": True,
            "device": target_dev,
            "reason": "Dispositivo non presente nel filesystem di blocco",
        }

    raw_data = None
    # Probe standard, SAT (SCSI-to-ATA for USB enclosures), and auto
    probe_commands = [
        [smartctl, "-j", "-i", "-H", "-A", target_dev],
        [smartctl, "-d", "sat", "-j", "-i", "-H", "-A", target_dev],
        [smartctl, "-d", "auto", "-j", "-i", "-H", "-A", target_dev],
    ]

    for cmd in probe_commands:
        res = runner.run(cmd, timeout=3.0)
        if res.stdout and ("smart_status" in res.stdout or "device" in res.stdout or "model_name" in res.stdout):
            try:
                raw_data = json.loads(res.stdout)
                break
            except json.JSONDecodeError:
                continue

    if not raw_data:
        return {
            "supported": False,
            "installed": True,
            "device": target_dev,
            "reason": "S.M.A.R.T. non esposto dal bridge USB",
        }

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
        "serial": serial,
    }

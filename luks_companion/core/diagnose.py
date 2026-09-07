"""
System Diagnosis Engine for luks-companion.
Runs full end-to-end health audits across hardware, LVM, LUKS, network, and systemd.
Eliminates opaque error guessing with actionable triage suggestions.
"""
import os
import sys
import time
import shutil
from typing import Dict, Any, List, Optional

from .config import Config
from .runner import SystemRunner, SubprocessRunner
from .ha_client import HomeAssistantClient
from .smart import find_smartctl_bin, get_smart_telemetry


def run_full_diagnosis(
    config: Config,
    runner: Optional[SystemRunner] = None,
    ha_client: Optional[HomeAssistantClient] = None,
) -> Dict[str, Any]:
    runner = runner or SubprocessRunner()
    ha = ha_client or HomeAssistantClient(config)
    report: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": {},
        "issues": [],
        "suggestions": [],
    }

    # 1. PERMISSIONS & ENVIRONMENT
    is_root = hasattr(os, "getuid") and os.getuid() == 0
    env_exists = runner.path_exists(config.env_file_path) if config.env_file_path else False
    sock_exists = runner.path_exists(config.socket_path)

    sock_info = "non presente"
    if sock_exists:
        try:
            st = os.stat(config.socket_path)
            import stat
            mode = oct(stat.S_IMODE(st.st_mode))
            sock_info = f"attivo (mode: {mode}, uid: {st.st_uid}, gid: {st.st_gid})"
        except Exception:
            sock_info = "attivo (impossibile leggere permessi)"

    report["checks"]["environment"] = {
        "is_root": is_root,
        "env_file": config.env_file_path,
        "env_exists": env_exists,
        "socket_path": config.socket_path,
        "socket_status": sock_info,
    }
    if not env_exists:
        report["issues"].append(f"File di configurazione non trovato: {config.env_file_path}")
        report["suggestions"].append("Creare il file .env partendo da config/config.env o install.sh")

    # 2. HOME ASSISTANT
    ha_status = {
        "enabled": config.enable_home_assistant,
        "url": config.ha_url,
        "entity_id": config.ha_entity_id,
        "reachable": False,
        "latency_ms": None,
        "state": "unknown",
    }
    if config.enable_home_assistant:
        t0 = time.time()
        state = ha.get_state(force_refresh=True)
        latency = round((time.time() - t0) * 1000, 1)
        ha_status["state"] = state
        ha_status["latency_ms"] = latency
        ha_status["reachable"] = state != "unknown"

        if state == "unknown":
            report["issues"].append("Home Assistant non risponde o il token non è valido.")
            report["suggestions"].append("Verificare connettività a HA_URL e validità del Bearer Token in .env")
    report["checks"]["home_assistant"] = ha_status

    # 3. PHYSICAL DISK & USB
    target_dev = None
    if config.target_dev and runner.path_exists(config.target_dev):
        target_dev = config.target_dev
    else:
        # Search via LVM PV
        pvs_res = runner.run(["pvs", "--noheadings", "-o", "pv_name", "-S", f"vg_name={config.vg_name}"], timeout=2.0)
        if pvs_res.ok and pvs_res.stdout.strip():
            pv = pvs_res.stdout.strip().splitlines()[0].strip()
            lsblk_res = runner.run(["lsblk", "-s", "-rno", "PATH,TYPE", pv], timeout=2.0)
            if lsblk_res.ok and lsblk_res.stdout:
                for pline in lsblk_res.stdout.splitlines():
                    parts = pline.strip().split()
                    if len(parts) >= 2 and parts[1].lower() == "disk" and runner.path_exists(parts[0]):
                        target_dev = parts[0]
                        break
            if not target_dev:
                target_dev = pv

    smart_bin = find_smartctl_bin()
    smart_info = get_smart_telemetry(target_dev, runner)

    report["checks"]["hardware"] = {
        "target_dev": target_dev,
        "disk_detected": bool(target_dev and runner.path_exists(target_dev)),
        "smartctl_available": bool(smart_bin),
        "smart": smart_info,
    }

    if not target_dev or not runner.path_exists(target_dev):
        if config.enable_home_assistant and ha_status.get("state") != "on":
            report["issues"].append("Disco fisico non rilevato sul bus: la presa Home Assistant risulta spenta.")
            report["suggestions"].append("Accendere la presa con 'luks-companion start' o tramite app Home Assistant.")
        else:
            report["issues"].append("Disco fisico non rilevato sul bus USB benché alimentato.")
            report["suggestions"].append("Verificare il cavo USB del box Western Digital ed esaminare 'dmesg -T | tail -n 20'.")

    # 4. LVM & LOGICAL VOLUMES
    vg_exists = False
    vg_active = False
    vgs_res = runner.run(["vgs", "--noheadings", "-o", "vg_name", config.vg_name], timeout=2.0)
    if vgs_res.ok and config.vg_name in vgs_res.stdout:
        vg_exists = True
        vg_dir = f"/dev/{config.vg_name}"
        vg_active = runner.path_exists(vg_dir) and runner.is_dir(vg_dir)

    lv_crypto_exists = runner.path_exists(config.lv_crypto_path)
    lv_backup_exists = runner.path_exists(config.lv_backup_path) if config.lv_backup else False

    report["checks"]["lvm"] = {
        "vg_name": config.vg_name,
        "vg_exists": vg_exists,
        "vg_active": vg_active,
        "lv_crypto_path": config.lv_crypto_path,
        "lv_crypto_present": lv_crypto_exists,
        "lv_backup_path": config.lv_backup_path,
        "lv_backup_present": lv_backup_exists,
    }

    # 5. LUKS MAPPER
    mapper_open = runner.path_exists(config.mapper_path)
    luks_version = "unknown"
    if lv_crypto_exists:
        luks_dump = runner.run(["cryptsetup", "luksDump", config.lv_crypto_path], timeout=3.0)
        if luks_dump.ok and "Version:" in luks_dump.stdout:
            for line in luks_dump.stdout.splitlines():
                if "Version:" in line:
                    luks_version = line.split(":", 1)[1].strip()
                    break

    report["checks"]["luks"] = {
        "mapper_name": config.mapper_name,
        "mapper_path": config.mapper_path,
        "is_open": mapper_open,
        "luks_version": luks_version,
    }

    # 6. MOUNTPOINTS & PROCESSES
    crypto_mounted = runner.is_mountpoint(config.mount_crypto)
    backup_mounted = runner.is_mountpoint(config.mount_backup) if config.mount_backup else False

    blocking_procs = []
    if crypto_mounted:
        fuser_res = runner.run(["fuser", "-m", config.mount_crypto], timeout=2.0)
        if fuser_res.stdout.strip():
            blocking_procs = fuser_res.stdout.strip().split()

    report["checks"]["mounts"] = {
        "mount_crypto": config.mount_crypto,
        "crypto_mounted": crypto_mounted,
        "mount_backup": config.mount_backup,
        "backup_mounted": backup_mounted,
        "blocking_pids_on_crypto": blocking_procs,
    }

    # 7. SYSTEMD SERVICES
    services = {}
    for srv in ["luks-managerd", "luks-web", "webdav"]:
        s_res = runner.run(["systemctl", "is-active", srv], timeout=2.0)
        services[srv] = s_res.stdout.strip() if s_res.stdout.strip() else ("active" if s_res.ok else "inactive")
    report["checks"]["services"] = services

    return report


def format_diagnosis_cli(report: Dict[str, Any]) -> str:
    lines = [
        "============================================================",
        f"  LUKS-COMPANION: REPORT DIAGNOSTICO COMPLETO ({report['timestamp']})",
        "============================================================",
    ]

    checks = report["checks"]

    # 1. Environment
    env = checks.get("environment", {})
    root_icon = "✓" if env.get("is_root") else "!"
    lines.append(f"[{root_icon}] Permessi Root:       {'Sì (EUID 0)' if env.get('is_root') else 'No (richiesto sudo per avvio/arresto)'}")
    lines.append(f"    File Config:      {env.get('env_file')} ({'Presente' if env.get('env_exists') else 'MANCANTE'})")
    lines.append(f"    IPC Socket:       {env.get('socket_path')} -> {env.get('socket_status')}")

    # 2. Home Assistant
    ha = checks.get("home_assistant", {})
    if ha.get("enabled"):
        ha_icon = "✓" if ha.get("reachable") else "❌"
        lines.append(f"[{ha_icon}] Home Assistant:     {ha.get('state').upper()} ({ha.get('url')}) - Latenza: {ha.get('latency_ms')}ms")
    else:
        lines.append("[*] Home Assistant:     Disabilitato (Always-on)")

    # 3. Hardware & SMART
    hw = checks.get("hardware", {})
    hw_icon = "✓" if hw.get("disk_detected") else "❌"
    dev_str = hw.get("target_dev") or "Nessun disco rilevato"
    lines.append(f"[{hw_icon}] Disco Fisico:        {dev_str}")
    smart = hw.get("smart", {})
    if smart.get("supported"):
        lines.append(f"    S.M.A.R.T. Stato: {smart.get('health')} | Temp: {smart.get('temperature_c')}°C | Modello: {smart.get('model')}")
    else:
        lines.append(f"    S.M.A.R.T. Info:  {smart.get('reason', 'Non disponibile')}")

    # 4. LVM & LUKS
    lvm = checks.get("lvm", {})
    lvm_icon = "✓" if lvm.get("vg_active") else ("!" if lvm.get("vg_exists") else "❌")
    lines.append(f"[{lvm_icon}] LVM Volume Group:    {lvm.get('vg_name')} ({'Attivo' if lvm.get('vg_active') else ('Presente ma inattivo' if lvm.get('vg_exists') else 'Non trovato')})")

    luks = checks.get("luks", {})
    luks_icon = "✓" if luks.get("is_open") else "🔒"
    lines.append(f"[{luks_icon}] Mapper Crittografico: {luks.get('mapper_name')} ({'Aperto' if luks.get('is_open') else 'Chiuso'}) [LUKS {luks.get('luks_version')}]")

    # 5. Mounts
    mnt = checks.get("mounts", {})
    mnt_icon = "✓" if mnt.get("crypto_mounted") else "○"
    lines.append(f"[{mnt_icon}] Mount Principale:    {mnt.get('mount_crypto')} ({'Montato' if mnt.get('crypto_mounted') else 'Non montato'})")
    if mnt.get("mount_backup"):
        b_icon = "✓" if mnt.get("backup_mounted") else "○"
        lines.append(f"[{b_icon}] Mount Backup:        {mnt.get('mount_backup')} ({'Montato' if mnt.get('backup_mounted') else 'Non montato'})")

    # 6. Services
    srvs = checks.get("services", {})
    lines.append(
        f"[*] Servizi Systemd:    luks-managerd: {srvs.get('luks-managerd')} | luks-web: {srvs.get('luks-web')} | webdav: {srvs.get('webdav')}"
    )

    # ISSUES AND SUGGESTIONS
    if report["issues"]:
        lines.append("\n------------------------------------------------------------")
        lines.append("  ⚠️ ANOMALIE RILEVATE:")
        for idx, issue in enumerate(report["issues"], 1):
            lines.append(f"  {idx}. {issue}")
        lines.append("\n  💡 AZIONI SUGGERITE:")
        for idx, sugg in enumerate(report["suggestions"], 1):
            lines.append(f"  {idx}. {sugg}")
    else:
        lines.append("\n------------------------------------------------------------")
        lines.append("  ✓ TUTTI I CONTROLLI DI SISTEMA SONO NOMINALI.")

    lines.append("============================================================")
    return "\n".join(lines)

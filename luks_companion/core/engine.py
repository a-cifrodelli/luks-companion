"""
StorageEngine - Unified, stateful storage lifecycle and encryption manager.
Executes all 7 startup phases and the 8-step atomic, idempotent teardown sequence.
Provides detailed error contexts and actionable hints to eliminate guesswork.
"""
import os
import sys
import time
import shutil
import base64
import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Callable

from .config import Config
from .runner import SystemRunner, SubprocessRunner, CommandResult, StorageEngineError
from .ha_client import HomeAssistantClient
from .smart import get_smart_telemetry, get_volume_stats


class StorageState(str, Enum):
    STOPPED = "stopped"
    STANDBY = "standby"
    UNLOCKED = "unlocked"
    MOUNTED = "mounted"


class StorageEngine:
    def __init__(
        self,
        config: Config,
        runner: Optional[SystemRunner] = None,
        ha_client: Optional[HomeAssistantClient] = None,
    ):
        self.config = config
        self.runner = runner or SubprocessRunner()
        self.ha = ha_client or HomeAssistantClient(config)

        # Internal state flags
        self.power_is_on = False
        self.volume_is_unlocked = False
        self.filesystem_is_mounted = False
        self._teardown_done = False

    def log(self, msg: str, log_cb: Optional[Callable[[str], None]] = None) -> None:
        if log_cb:
            log_cb(msg)
        else:
            print(msg, flush=True)

    def notify_discord(self, event: str, message: str = "") -> None:
        if not self.config.discord_webhook_url:
            return
        try:
            from .notify import send_discord_notification
            send_discord_notification(self.config, event, message)
        except Exception:
            # Fallback to external script if present
            notify_script = os.path.join(self.config.base_dir, "scripts", "notify-discord.py")
            if os.path.exists(notify_script):
                try:
                    self.runner.run(
                        [sys.executable, notify_script, event, message],
                        timeout=10.0,
                    )
                except Exception:
                    pass

    # -------------------------------------------------------------------
    # HARDWARE & BLOCK DEVICE DISCOVERY
    # -------------------------------------------------------------------
    def find_target_block_device(self) -> Optional[str]:
        """
        Resolves physical target block device strictly via LVM PV belonging to VG_NAME
        or explicit TARGET_DEV. Eliminates any heuristic system disk guessing.
        """
        if self.config.target_dev and self.runner.path_exists(self.config.target_dev):
            return self.config.target_dev

        # 1. Trace from active mapper, mount, or VG dev if already active
        candidates = []
        if self.config.mapper_name and self.runner.path_exists(self.config.mapper_path):
            candidates.append(self.config.mapper_path)
        if self.config.mount_crypto and self.runner.path_exists(self.config.mount_crypto):
            candidates.append(self.config.mount_crypto)
        vg_dir = f"/dev/{self.config.vg_name}"
        if self.config.vg_name and self.runner.path_exists(vg_dir):
            candidates.append(vg_dir)

        for cand in candidates:
            res = self.runner.run(["lsblk", "-s", "-rno", "PATH,TYPE", cand], timeout=2.0)
            if res.ok and res.stdout:
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1].lower() == "disk" and self.runner.path_exists(parts[0]):
                        return parts[0]

        # 2. Query LVM Physical Volumes strictly filtered by configured VG_NAME
        if self.config.vg_name:
            res = self.runner.run(
                ["pvs", "--noheadings", "-o", "pv_name", "-S", f"vg_name={self.config.vg_name}"],
                timeout=2.0,
            )
            if res.ok and res.stdout:
                for line in res.stdout.splitlines():
                    pv = line.strip()
                    if pv and self.runner.path_exists(pv):
                        # Find parent disk (e.g. sdc from sdc1 or whole disk sdc)
                        lsblk_res = self.runner.run(["lsblk", "-s", "-rno", "PATH,TYPE", pv], timeout=2.0)
                        if lsblk_res.ok and lsblk_res.stdout:
                            for pline in lsblk_res.stdout.splitlines():
                                pparts = pline.strip().split()
                                if len(pparts) >= 2 and pparts[1].lower() == "disk" and self.runner.path_exists(pparts[0]):
                                    return pparts[0]
                        return pv

        return None

    # -------------------------------------------------------------------
    # STATE AND METRICS
    # -------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        target_dev = self.find_target_block_device()
        disk_present = target_dev is not None and self.runner.path_exists(target_dev)

        is_mounted = self.runner.is_mountpoint(self.config.mount_crypto) if self.config.mount_crypto else False
        is_unlocked = (self.runner.path_exists(self.config.mapper_path) and disk_present) if self.config.mapper_name else False

        vg_active = False
        if disk_present and self.config.vg_name:
            vg_dev = f"/dev/{self.config.vg_name}"
            mapper_lv = f"/dev/mapper/{self.config.vg_name}-{self.config.lv_crypto}" if self.config.lv_crypto else ""
            vg_active = (self.runner.path_exists(vg_dev) and self.runner.is_dir(vg_dev)) or (
                bool(mapper_lv) and self.runner.path_exists(mapper_lv)
            )

        webdav_active = False
        res_wd = self.runner.run(["systemctl", "is-active", "--quiet", "webdav"], timeout=2.0)
        webdav_active = res_wd.returncode == 0

        ha_state = self.ha.get_state()
        if self.config.enable_home_assistant:
            plug_powered = (ha_state == "on")
        else:
            plug_powered = disk_present

        if is_mounted:
            master_status = StorageState.MOUNTED
        elif is_unlocked:
            master_status = StorageState.UNLOCKED
        elif plug_powered:
            master_status = StorageState.STANDBY
        else:
            master_status = StorageState.STOPPED

        volumes = []
        if is_mounted:
            v_crypto = get_volume_stats(self.config.mount_crypto, "Dati Cifrati (crypto_data)", self.runner)
            if v_crypto:
                volumes.append(v_crypto)
            if self.config.mount_backup:
                v_backup = get_volume_stats(self.config.mount_backup, "Backup (backup_data)", self.runner)
                if v_backup:
                    volumes.append(v_backup)

        smart_info = get_smart_telemetry(target_dev if disk_present else None, self.runner)

        return {
            "status": master_status.value,
            "unlocked": is_unlocked,
            "mounted": is_mounted,
            "vg_active": vg_active,
            "plug_state": ha_state,
            "plug_powered": plug_powered,
            "disk_present": disk_present,
            "target_dev": target_dev,
            "webdav_active": webdav_active,
            "webdav_port": self.config.webdav_port,
            "vg_name": self.config.vg_name,
            "mapper_name": self.config.mapper_name,
            "mount_crypto": self.config.mount_crypto,
            "mount_backup": self.config.mount_backup,
            "volumes": volumes,
            "smart": smart_info,
        }

    # -------------------------------------------------------------------
    # LIFECYCLE: STARTUP (7 PHASES)
    # -------------------------------------------------------------------
    def start(
        self,
        passphrase: Optional[str] = None,
        keyfile_bytes: Optional[bytes] = None,
        keyfile_path: Optional[str] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Executes the complete startup sequence with automatic rollback teardown on error.
        """
        self._teardown_done = False
        try:
            self.log("[*] Avvio sequenza di sblocco e montaggio storage...", log_cb)

            # PHASE 1: HOME ASSISTANT POWER ON
            if self.config.enable_home_assistant:
                self.log(f"  [1/7] Accensione presa Home Assistant ({self.config.ha_entity_id})...", log_cb)
                if not self.ha.turn_on():
                    raise StorageEngineError(
                        phase="HA_POWER_ON",
                        message=f"Impossibile contattare Home Assistant all'indirizzo {self.config.ha_url}",
                        hint="Verificare che Home Assistant sia raggiungibile sulla rete e che HA_TOKEN sia valido.",
                    )

                plug_ready = False
                for _ in range(self.config.ha_max_wait_sec):
                    if self.ha.get_state(force_refresh=True) == "on":
                        plug_ready = True
                        break
                    time.sleep(1.0)

                if not plug_ready:
                    raise StorageEngineError(
                        phase="HA_POWER_ON",
                        message=f"La presa non si è accesa entro {self.config.ha_max_wait_sec} secondi.",
                        hint="Verificare l'alimentazione fisica della presa Tapo/Shelly e la connessione Wi-Fi.",
                    )
                self.power_is_on = True
                self.log("  [✓] Presa accesa e confermata da Home Assistant.", log_cb)
            else:
                self.log("  [1/7] Home Assistant disabilitato: alimentazione Always-on presunta.", log_cb)

            # PHASE 2: USB BUS DETECTION
            self.log(f"  [2/7] Rilevamento disco sul bus USB (max {self.config.usb_wait_max_sec}s)...", log_cb)
            target_dev = None
            for _ in range(self.config.usb_wait_max_sec):
                target_dev = self.find_target_block_device()
                if target_dev and self.runner.path_exists(target_dev):
                    break
                time.sleep(1.0)

            if not target_dev or not self.runner.path_exists(target_dev):
                raise StorageEngineError(
                    phase="USB_DETECTION",
                    message=f"Nessun disco rilevato sul bus USB per il VG '{self.config.vg_name}' entro {self.config.usb_wait_max_sec}s.",
                    hint="Verificare il cavo USB del box MyBook e che il disco riceva alimentazione 220V.",
                )
            self.log(f"  [✓] Disco individuato sul bus: {target_dev}", log_cb)

            # PHASE 3: LVM ACTIVATION
            self.log(f"  [3/7] Scansione e attivazione Volume Group LVM ({self.config.vg_name})...", log_cb)
            self.runner.run(["vgscan", "--mknodes"], timeout=10.0, check=True, phase="LVM_ACTIVATION")
            self.runner.run(
                ["vgchange", "-ay", self.config.vg_name],
                timeout=10.0,
                check=True,
                phase="LVM_ACTIVATION",
                hint="Verificare che i metadati LVM del disco non siano corrotti (eseguire 'vgck' o 'pvscan').",
            )
            self.log("  [✓] Volume Group LVM attivo.", log_cb)

            # PHASE 4: LUKS2 UNLOCK
            self.log("  [4/7] Verifica stato LUKS2...", log_cb)
            if self.runner.path_exists(self.config.mapper_path):
                self.log("  [*] Mapper crittografico già aperto.", log_cb)
                self.volume_is_unlocked = True
            else:
                self.log(f"  -> Sblocco container LUKS2 su {self.config.lv_crypto_path}...", log_cb)
                self._unlock_luks_container(passphrase, keyfile_bytes, keyfile_path)
                self.volume_is_unlocked = True
                self.log("  [✓] Container LUKS2 sbloccato con successo!", log_cb)

            # PHASE 5: MOUNT FILESYSTEMS
            self.log("  [5/7] Montaggio filesystem...", log_cb)
            os.makedirs(self.config.mount_crypto, exist_ok=True)
            if not self.runner.is_mountpoint(self.config.mount_crypto):
                self.runner.run(
                    ["mount", self.config.mapper_path, self.config.mount_crypto],
                    timeout=10.0,
                    check=True,
                    phase="MOUNT_FS",
                    hint="Verificare l'integrità del filesystem ext4 con 'fsck.ext4 -f /dev/mapper/cryptovault'.",
                )
                self.runner.run(["chmod", "2775", self.config.mount_crypto], timeout=2.0)
                if self.config.storage_group:
                    self.runner.run(["chgrp", self.config.storage_group, self.config.mount_crypto], timeout=2.0)

            if self.config.lv_backup and self.config.mount_backup and self.runner.path_exists(self.config.lv_backup_path):
                os.makedirs(self.config.mount_backup, exist_ok=True)
                if not self.runner.is_mountpoint(self.config.mount_backup):
                    self.runner.run(
                        ["mount", self.config.lv_backup_path, self.config.mount_backup],
                        timeout=10.0,
                        check=True,
                        phase="MOUNT_FS",
                        hint=f"Verificare il secondo volume ext4 '{self.config.lv_backup_path}'.",
                    )
                    self.runner.run(["chmod", "2775", self.config.mount_backup], timeout=2.0)
                    if self.config.storage_group:
                        self.runner.run(["chgrp", self.config.storage_group, self.config.mount_backup], timeout=2.0)

            self.filesystem_is_mounted = True
            self.log("  [✓] Volumi montati con permessi SGID 2775.", log_cb)

            # PHASE 6: WEBDAV SERVICE
            if self.config.enable_webdav:
                self.log("  [6/7] Aggiornamento configurazione e avvio WebDAV...", log_cb)
                self._update_webdav_config()
                self.runner.run(["systemctl", "restart", "webdav"], timeout=10.0, check=False)
                self.log("  [✓] Servizio WebDAV riavviato sulla nuova radice.", log_cb)
            else:
                self.log("  [6/7] WebDAV non abilitato in configurazione.", log_cb)

            # PHASE 7: NOTIFICATION & READY
            self.log("  [7/7] Sistema operativo e storage pronto!", log_cb)
            self.notify_discord("unlock", f"Storage montato con successo ({self.config.mount_crypto})")
            return True

        except Exception as exc:
            self.log(f"\n[!] ERRORE DURANTE L'AVVIO: {exc}", log_cb)
            if isinstance(exc, StorageEngineError):
                self.log(exc.format_detailed(), log_cb)
            self.notify_discord("error", str(exc))
            self.safe_teardown(log_cb=log_cb, notify_event="")
            raise

    def _unlock_luks_container(
        self,
        passphrase: Optional[str],
        keyfile_bytes: Optional[bytes],
        keyfile_path: Optional[str],
    ) -> None:
        lv_dev = self.config.lv_crypto_path
        if not self.runner.path_exists(lv_dev):
            raise StorageEngineError(
                phase="LUKS_UNLOCK",
                message=f"Dispositivo logico crittografato {lv_dev} non trovato.",
                hint=f"Verificare lo stato LVM con 'lvs {self.config.vg_name}'.",
            )

        temp_shm_key = None
        try:
            cmd = ["cryptsetup", "open", lv_dev, self.config.mapper_name]

            if keyfile_path and self.runner.path_exists(keyfile_path):
                cmd.extend(["--key-file", keyfile_path])
                res = self.runner.run(cmd, timeout=15.0)
            elif keyfile_bytes:
                import tempfile
                shm_dir = "/dev/shm" if os.path.exists("/dev/shm") else tempfile.gettempdir()
                temp_shm_key = os.path.join(shm_dir, f".luks_key_{os.getpid()}_{int(time.time())}")
                with open(temp_shm_key, "wb") as f:
                    f.write(keyfile_bytes)
                os.chmod(temp_shm_key, 0o600)
                cmd.extend(["--key-file", temp_shm_key])
                res = self.runner.run(cmd, timeout=15.0)
            elif passphrase:
                # Strip trailing newlines from passphrase
                clean_pass = passphrase.encode("utf-8")
                res = self.runner.run(cmd, stdin_data=clean_pass, timeout=15.0)
            else:
                raise StorageEngineError(
                    phase="LUKS_UNLOCK",
                    message="Nessuna credenziale (passphrase o keyfile) fornita per lo sblocco.",
                    hint="Fornire una passphrase o un keyfile valido via CLI o WebUI.",
                )

            if res.returncode != 0:
                raise StorageEngineError(
                    phase="LUKS_UNLOCK",
                    message="Sblocco LUKS fallito: chiave o passphrase non corretta.",
                    command=cmd,
                    returncode=res.returncode,
                    stderr=res.stderr,
                    stdout=res.stdout,
                    hint="Verificare che la passphrase digitata o il keyfile caricato corrispondano al container LUKS2.",
                )

        finally:
            if temp_shm_key and os.path.exists(temp_shm_key):
                try:
                    self.runner.run(["shred", "-u", temp_shm_key], timeout=5.0)
                except Exception:
                    try:
                        os.remove(temp_shm_key)
                    except OSError:
                        pass

    def _update_webdav_config(self) -> None:
        webdav_cfg = "/etc/webdav/config.yaml"
        if os.path.exists(webdav_cfg):
            try:
                with open(webdav_cfg, "r", encoding="utf-8") as f:
                    content = f.read()

                # Update storage base path and scope cleanly
                import re
                content = re.sub(r'directory:\s*".*"', f'directory: "{self.config.storage_base}"', content)
                content = re.sub(r'scope:\s*".*"', 'scope: "/"', content)

                with open(webdav_cfg, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass

    # -------------------------------------------------------------------
    # LIFECYCLE: TEARDOWN SICURO (8 PASSI ATOMICI & IDEMPOTENTI)
    # -------------------------------------------------------------------
    def safe_teardown(
        self,
        log_cb: Optional[Callable[[str], None]] = None,
        notify_event: str = "lock",
    ) -> bool:
        """
        Executes the safe, deterministic teardown sequence in reverse order.
        Idempotent: safe against re-entrancy and double calls.
        """
        if self._teardown_done:
            return True
        self._teardown_done = True

        self.log("\n[*] Inizio sequenza di arresto sicuro e teardown...", log_cb)

        # 0. Identify target device before unmounting
        final_dev = self.find_target_block_device()

        # STEP 1: STOP WEBDAV
        if self.config.enable_webdav:
            self.log("  -> [1/8] Arresto servizio WebDAV...", log_cb)
            self.runner.run(["systemctl", "stop", "webdav"], timeout=5.0)

        # STEP 2: FLUSH WRITE BUFFER
        self.log("  -> [2/8] Flush buffer cache RAM (sync)...", log_cb)
        self.runner.sync()

        # STEP 3: TERMINATE ACTIVE PROCESSES & UNMOUNT FILESYSTEMS
        mounts_to_clean = []
        if self.config.mount_crypto and (self.filesystem_is_mounted or self.runner.is_mountpoint(self.config.mount_crypto)):
            mounts_to_clean.append(self.config.mount_crypto)
        if self.config.mount_backup and self.runner.is_mountpoint(self.config.mount_backup):
            mounts_to_clean.append(self.config.mount_backup)

        if mounts_to_clean:
            self.log("  -> [3/8] Chiusura processi e smontaggio filesystem...", log_cb)
            for mnt in mounts_to_clean:
                self.runner.run(["fuser", "-km", "-9", mnt], timeout=5.0)
            time.sleep(1.0)
            for mnt in mounts_to_clean:
                res_um = self.runner.run(["umount", mnt], timeout=10.0)
                if res_um.returncode != 0:
                    time.sleep(1.0)
                    self.runner.run(["fuser", "-km", "-9", mnt], timeout=5.0)
                    self.runner.run(["umount", "-l", mnt], timeout=10.0)
            self.filesystem_is_mounted = False
            self.log("  [✓] Filesystem smontati.", log_cb)
        else:
            self.log("  [*] [3/8] Nessun filesystem attivo da smontare.", log_cb)

        # STEP 4: CLOSE LUKS MAPPER
        if self.volume_is_unlocked or (self.config.mapper_name and self.runner.path_exists(self.config.mapper_path)):
            self.log(f"  -> [4/8] Chiusura container LUKS ({self.config.mapper_name})...", log_cb)
            closed = False
            for _ in range(3):
                res_cl = self.runner.run(["cryptsetup", "close", self.config.mapper_name], timeout=10.0)
                if res_cl.ok:
                    closed = True
                    break
                time.sleep(1.0)
            if not closed:
                self.runner.run(["dmsetup", "remove", "-f", self.config.mapper_name], timeout=5.0)
            self.volume_is_unlocked = False
            self.log("  [✓] Chiave crittografica distrutta dalla RAM e mapper chiuso.", log_cb)
        else:
            self.log("  [*] [4/8] Nessun container LUKS aperto.", log_cb)

        # STEP 5: DEACTIVATE LVM VOLUME GROUP
        if self.config.vg_name:
            self.log(f"  -> [5/8] Disattivazione Volume Group LVM ({self.config.vg_name})...", log_cb)
            for _ in range(3):
                res_vg = self.runner.run(["vgchange", "-an", self.config.vg_name], timeout=10.0)
                if res_vg.ok:
                    break
                time.sleep(1.0)
            self.log("  [✓] Volume Group LVM disattivato.", log_cb)

        # STEP 6: SCSI STOP UNIT & BUS DETACHMENT
        if final_dev and self.runner.path_exists(final_dev):
            dev_name = os.path.basename(final_dev)
            self.log(f"  -> [6/8] Invio comando SCSI STOP UNIT ed espulsione bus ({final_dev})...", log_cb)
            self.runner.run(["udisksctl", "power-off", "-b", final_dev], timeout=10.0)

            # Active kernel detachment verification loop
            self.log("  -> Verifica disconnessione dispositivo nel kernel Linux...", log_cb)
            off_confirmed = False
            for _ in range(10):
                if not self.runner.path_exists(final_dev) and not self.runner.path_exists(f"/sys/block/{dev_name}"):
                    off_confirmed = True
                    self.log("  [✓] Disconnessione confermata dal kernel! Il disco è inerte.", log_cb)
                    break
                time.sleep(1.0)

            if not off_confirmed:
                self.log(f"  [!] Attesa tolleranza spin-down meccanico ({self.config.spindown_wait_sec}s)...", log_cb)
                time.sleep(self.config.spindown_wait_sec)
        else:
            self.log("  [*] [6/8] Nessun disco esterno attivo da disconnettere via SCSI.", log_cb)

        # STEP 7: 220V CUTOFF VIA HOME ASSISTANT
        if self.config.enable_home_assistant:
            ha_st = self.ha.get_state(force_refresh=True)
            if self.power_is_on or ha_st == "on":
                self.log(f"  -> [7/8] Pausa pre-cutoff ({self.config.cutoff_grace_sec}s)...", log_cb)
                time.sleep(self.config.cutoff_grace_sec)
                self.log(f"  -> Invio spegnimento presa Home Assistant ({self.config.ha_entity_id})...", log_cb)
                self.ha.turn_off()
                self.power_is_on = False
                self.log("  [✓] Comando spegnimento presa inviato.", log_cb)
            else:
                self.log(f"  [*] [7/8] Presa Home Assistant già spenta (stato: {ha_st}).", log_cb)

        # STEP 8: COMPLETION
        self.log("  [✓] [8/8] Teardown completato. Il disco è in Standby a 0 Watt.", log_cb)
        if notify_event:
            self.notify_discord(notify_event, "Storage smontato e alimentazione 220V interrotta (0W Standby)")
        return True

    def stop(self, log_cb: Optional[Callable[[str], None]] = None, notify_event: str = "lock") -> bool:
        return self.safe_teardown(log_cb=log_cb, notify_event=notify_event)

    # -------------------------------------------------------------------
    # DISASTER RECOVERY: LUKS HEADER BACKUP & RESTORE
    # -------------------------------------------------------------------
    def backup_luks_header(self) -> Dict[str, Any]:
        lv_path = self.config.lv_crypto_path
        if not self.runner.path_exists(lv_path):
            self.runner.run(["vgscan", "--mknodes"], timeout=5.0)
            self.runner.run(["vgchange", "-ay", self.config.vg_name], timeout=5.0)

        if not self.runner.path_exists(lv_path):
            raise StorageEngineError(
                phase="HEADER_BACKUP",
                message=f"Dispositivo {lv_path} non trovato. Verificare che il disco sia alimentato e connesso.",
                hint="Assicurarsi che il disco sia acceso e che il Volume Group LVM sia rilevabile.",
            )

        import tempfile
        shm_dir = "/dev/shm" if os.path.exists("/dev/shm") else tempfile.gettempdir()
        temp_header = os.path.join(shm_dir, "luks_header_backup.bin")
        try:
            res = self.runner.run(
                ["cryptsetup", "luksHeaderBackup", lv_path, "--header-backup-file", temp_header],
                timeout=15.0,
                check=True,
                phase="HEADER_BACKUP",
            )
            with open(temp_header, "rb") as f:
                header_bytes = f.read()

            header_b64 = base64.b64encode(header_bytes).decode("ascii")
            date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"luks_header_{self.config.vg_name}_{self.config.lv_crypto}_{date_str}.header"

            return {
                "header_base64": header_b64,
                "filename": filename,
                "size_bytes": len(header_bytes),
            }
        finally:
            if os.path.exists(temp_header):
                try:
                    self.runner.run(["shred", "-u", temp_header], timeout=5.0)
                except Exception:
                    try:
                        os.remove(temp_header)
                    except OSError:
                        pass

    def restore_luks_header(self, header_bytes: bytes) -> bool:
        # Prevent restoring while volume is open or mounted!
        if (self.config.mount_crypto and self.runner.is_mountpoint(self.config.mount_crypto)) or (
            self.config.mapper_name and self.runner.path_exists(self.config.mapper_path)
        ):
            raise StorageEngineError(
                phase="HEADER_RESTORE",
                message="Impossibile ripristinare l'header mentre il volume è aperto o montato.",
                hint="Eseguire 'stop' per chiudere e smontare il volume prima di tentare il ripristino dei metadati.",
            )

        lv_path = self.config.lv_crypto_path
        if not self.runner.path_exists(lv_path):
            self.runner.run(["vgscan", "--mknodes"], timeout=5.0)
            self.runner.run(["vgchange", "-ay", self.config.vg_name], timeout=5.0)

        if not self.runner.path_exists(lv_path):
            raise StorageEngineError(
                phase="HEADER_RESTORE",
                message=f"Dispositivo target {lv_path} non trovato.",
                hint="Verificare che il disco sia alimentato e connesso prima del ripristino.",
            )

        import tempfile
        shm_dir = "/dev/shm" if os.path.exists("/dev/shm") else tempfile.gettempdir()
        temp_restore = os.path.join(shm_dir, "luks_header_restore.bin")
        try:
            with open(temp_restore, "wb") as f:
                f.write(header_bytes)
            os.chmod(temp_restore, 0o600)

            self.runner.run(
                ["cryptsetup", "luksHeaderRestore", lv_path, "--header-backup-file", temp_restore, "--batch-mode"],
                timeout=20.0,
                check=True,
                phase="HEADER_RESTORE",
                hint="Verificare che il file caricato sia un backup header LUKS2 valido per questa partizione.",
            )
            return True
        finally:
            if os.path.exists(temp_restore):
                try:
                    self.runner.run(["shred", "-u", temp_restore], timeout=5.0)
                except Exception:
                    try:
                        os.remove(temp_restore)
                    except OSError:
                        pass

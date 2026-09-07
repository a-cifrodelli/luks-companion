"""
Configuration manager for luks-companion.
Loads, parses, and validates .env parameters with typed fallbacks.
"""
from dataclasses import dataclass, field
import os
import posixpath
from typing import Optional, Dict, Any


@dataclass
class Config:
    # LVM and LUKS
    vg_name: str = "mybook"
    lv_crypto: str = "crypto_data"
    lv_backup: str = "backup_data"
    mapper_name: str = "cryptovault"
    storage_base: str = "/srv/storage"
    storage_user: str = "storage"
    storage_group: str = "storage"
    target_dev: str = ""

    # Home Assistant
    enable_home_assistant: bool = False
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    ha_entity_id: str = "switch.presa_disco"
    ha_max_wait_sec: int = 15

    # Timing and tolerances (in seconds)
    usb_wait_max_sec: int = 40
    spindown_wait_sec: int = 3
    cutoff_grace_sec: int = 5
    idle_timeout_min: int = 30

    # WebDAV and Web UI
    enable_webdav: bool = True
    webdav_port: int = 8081
    webdav_user: str = "admin"
    webdav_password_hash: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = 9099

    # Notifications & Sockets
    discord_webhook_url: str = ""
    socket_path: str = "/run/luks-manager.sock"
    socket_group: str = "luks-web"
    env_file_path: str = ""
    base_dir: str = ""

    # Dynamic paths
    @property
    def mount_crypto(self) -> str:
        return posixpath.join(self.storage_base, self.mapper_name)

    @property
    def mount_backup(self) -> str:
        return posixpath.join(self.storage_base, self.lv_backup) if self.lv_backup else ""

    @property
    def lv_crypto_path(self) -> str:
        return f"/dev/{self.vg_name}/{self.lv_crypto}"

    @property
    def lv_backup_path(self) -> str:
        return f"/dev/{self.vg_name}/{self.lv_backup}" if self.lv_backup else ""

    @property
    def mapper_path(self) -> str:
        return f"/dev/mapper/{self.mapper_name}"

    @classmethod
    def from_env_file(cls, env_path: Optional[str] = None) -> "Config":
        if env_path is None:
            # Default to repo root .env
            current_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = os.path.dirname(os.path.dirname(current_dir))
            env_path = os.path.join(base_dir, ".env")
        else:
            base_dir = os.path.dirname(os.path.abspath(env_path))

        raw_env: Dict[str, str] = {}
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, val = line.split("=", 1)
                            raw_env[key.strip()] = val.strip().strip('"').strip("'")
            except PermissionError:
                # Running as unprivileged process without read access to root 600 .env:
                # Gracefully fall back to defaults and process environment variables
                pass

        # Supplement with process environment variables
        for k, v in os.environ.items():
            if k not in raw_env:
                raw_env[k] = v

        return cls.from_dict(raw_env, env_path=env_path, base_dir=base_dir)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], env_path: str = "", base_dir: str = "") -> "Config":
        def to_bool(v: Any, default: bool = False) -> bool:
            if v is None:
                return default
            return str(v).strip().lower() in ("true", "1", "yes", "on")

        def to_int(v: Any, default: int) -> int:
            try:
                return int(str(v).strip())
            except (ValueError, TypeError):
                return default

        return cls(
            vg_name=data.get("VG_NAME", "mybook").strip(),
            lv_crypto=data.get("LV_CRYPTO", "crypto_data").strip(),
            lv_backup=data.get("LV_BACKUP", "backup_data").strip(),
            mapper_name=data.get("MAPPER_NAME", "cryptovault").strip(),
            storage_base=data.get("STORAGE_BASE", "/srv/storage").strip(),
            storage_user=data.get("STORAGE_USER", "storage").strip(),
            storage_group=data.get("STORAGE_GROUP", "storage").strip(),
            target_dev=data.get("TARGET_DEV", "").strip(),
            enable_home_assistant=to_bool(data.get("ENABLE_HOME_ASSISTANT"), False),
            ha_url=data.get("HA_URL", "http://homeassistant.local:8123").strip(),
            ha_token=data.get("HA_TOKEN", "").strip(),
            ha_entity_id=data.get("HA_ENTITY_ID", "switch.presa_disco").strip(),
            ha_max_wait_sec=to_int(data.get("HA_MAX_WAIT_SEC"), 15),
            usb_wait_max_sec=to_int(data.get("USB_WAIT_MAX_SEC"), 40),
            spindown_wait_sec=to_int(data.get("SPINDOWN_WAIT_SEC"), 3),
            cutoff_grace_sec=to_int(data.get("CUTOFF_GRACE_SEC"), 5),
            idle_timeout_min=to_int(data.get("IDLE_TIMEOUT_MIN"), 30),
            enable_webdav=to_bool(data.get("ENABLE_WEBDAV"), True),
            webdav_port=to_int(data.get("WEBDAV_PORT"), 8081),
            webdav_user=data.get("WEBDAV_USER", "admin").strip(),
            webdav_password_hash=data.get("WEBDAV_PASSWORD_HASH", "").strip(),
            web_host=data.get("WEB_HOST", "0.0.0.0").strip(),
            web_port=to_int(data.get("WEB_PORT"), 9099),
            discord_webhook_url=data.get("DISCORD_WEBHOOK_URL", "").strip(),
            socket_path=data.get("SOCKET_PATH", "/run/luks-manager.sock").strip(),
            socket_group=data.get("SOCKET_GROUP", "luks-web").strip(),
            env_file_path=env_path,
            base_dir=base_dir,
        )

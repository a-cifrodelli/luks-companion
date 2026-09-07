"""
Pytest configuration, fixtures, and in-memory mock system runner.
Enables 100% test coverage without touching physical hardware or root permissions.
"""
import pytest
from typing import Dict, List, Optional, Callable, Any
from luks_companion.core.config import Config
from luks_companion.core.runner import SystemRunner, CommandResult, StorageEngineError
from luks_companion.core.ha_client import HomeAssistantClient


class MockSystemRunner(SystemRunner):
    def __init__(self):
        self.commands_history: List[List[str]] = []
        self.command_responses: Dict[str, CommandResult] = {}
        self.existing_paths: set = set()
        self.directories: set = set()
        self.mountpoints: set = set()
        self.block_devices: set = set()
        self.sync_calls = 0

    def set_command_response(
        self,
        command_prefix: str,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ):
        self.command_responses[command_prefix] = CommandResult(
            cmd=[command_prefix],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def run(
        self,
        cmd: List[str],
        timeout: Optional[float] = None,
        stdin_data: Optional[bytes] = None,
        check: bool = False,
        phase: str = "SYSTEM",
        hint: str = "",
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        self.commands_history.append(cmd)
        cmd_str = " ".join(cmd)

        # Check matched prefixes
        res = None
        for prefix, canned in self.command_responses.items():
            if cmd_str.startswith(prefix) or (cmd and cmd[0] == prefix):
                res = CommandResult(
                    cmd=cmd,
                    returncode=canned.returncode,
                    stdout=canned.stdout,
                    stderr=canned.stderr,
                )
                break

        if res is None:
            # Default success response
            res = CommandResult(cmd=cmd, returncode=0, stdout="", stderr="")

        # Realistic mock state updates
        if res.ok:
            if len(cmd) >= 3 and cmd[0] == "udisksctl" and cmd[1] == "power-off":
                self.existing_paths.discard(cmd[-1])
                self.block_devices.discard(cmd[-1])
            elif len(cmd) >= 3 and cmd[0] == "cryptsetup" and cmd[1] == "close":
                self.existing_paths.discard(f"/dev/mapper/{cmd[2]}")
            elif len(cmd) >= 2 and cmd[0] == "umount":
                self.mountpoints.discard(cmd[-1])
            elif len(cmd) >= 3 and cmd[0] == "mount":
                self.mountpoints.add(cmd[-1])
                self.existing_paths.add(cmd[-1])
            elif len(cmd) >= 4 and cmd[0] == "cryptsetup" and cmd[1] == "open":
                self.existing_paths.add(f"/dev/mapper/{cmd[3]}")

        if log_callback and res.stdout:
            for line in res.stdout.splitlines():
                log_callback(line)

        if check and res.returncode != 0:
            raise StorageEngineError(
                phase=phase,
                message=f"Mock command '{cmd[0]}' failed with code {res.returncode}",
                command=cmd,
                returncode=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
                hint=hint,
            )

        return res

    def path_exists(self, path: str) -> bool:
        return path in self.existing_paths

    def is_dir(self, path: str) -> bool:
        return path in self.directories or path in self.existing_paths

    def is_block_device(self, path: str) -> bool:
        return path in self.block_devices or path in self.existing_paths

    def is_mountpoint(self, path: str) -> bool:
        return path in self.mountpoints

    def sync(self) -> None:
        self.sync_calls += 1


class MockHomeAssistantClient(HomeAssistantClient):
    def __init__(self, config: Config, initial_state: str = "on"):
        super().__init__(config)
        self.current_state = initial_state
        self.turn_on_calls = 0
        self.turn_off_calls = 0
        self.should_fail = False

    def get_state(self, force_refresh: bool = False) -> str:
        if self.should_fail:
            return "unknown"
        return self.current_state

    def turn_on(self) -> bool:
        if self.should_fail:
            return False
        self.turn_on_calls += 1
        self.current_state = "on"
        return True

    def turn_off(self) -> bool:
        if self.should_fail:
            return False
        self.turn_off_calls += 1
        self.current_state = "off"
        return True


@pytest.fixture
def mock_config():
    return Config(
        vg_name="mybook",
        lv_crypto="crypto_data",
        lv_backup="backup_data",
        mapper_name="cryptovault",
        storage_base="/srv/storage",
        enable_home_assistant=True,
        ha_url="http://mock-ha:8123",
        ha_token="mock-token",
        ha_entity_id="switch.presa_disco",
        ha_max_wait_sec=2,
        usb_wait_max_sec=2,
        spindown_wait_sec=0,
        cutoff_grace_sec=0,
        enable_webdav=True,
    )


@pytest.fixture
def mock_runner():
    return MockSystemRunner()


@pytest.fixture
def mock_ha(mock_config):
    return MockHomeAssistantClient(mock_config)

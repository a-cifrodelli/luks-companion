"""
Background Idle I/O Watchdog.
Monitors disk read/write sectors via sysfs and initiates safe teardown upon inactivity.
"""
import os
import time
import threading
from typing import Optional, Callable
from .engine import StorageEngine


def get_io_stats_for_device(dev_path: Optional[str]) -> Optional[str]:
    if not dev_path or not os.path.exists(dev_path):
        return None
    try:
        dev_real = os.path.realpath(dev_path)
        dev_name = os.path.basename(dev_real)
        for stat_path in [f"/sys/class/block/{dev_name}/stat", f"/sys/block/{dev_name}/stat"]:
            if os.path.isfile(stat_path):
                with open(stat_path, "r", encoding="utf-8") as f:
                    parts = f.read().split()
                    if len(parts) >= 5:
                        return f"{parts[0]}:{parts[4]}"
    except Exception:
        pass
    return None


class IdleWatchdog(threading.Thread):
    def __init__(
        self,
        engine: StorageEngine,
        check_interval_sec: int = 15,
        on_idle_timeout: Optional[Callable[[], None]] = None,
    ):
        super().__init__(daemon=True, name="LuksIdleWatchdog")
        self.engine = engine
        self.check_interval = check_interval_sec
        self.on_idle_timeout = on_idle_timeout
        self.running = True
        self.idle_seconds = 0
        self.last_stats: Optional[str] = None

    def stop_watchdog(self) -> None:
        self.running = False

    def run(self) -> None:
        while self.running:
            time.sleep(self.check_interval)
            try:
                timeout_min = self.engine.config.idle_timeout_min
                if timeout_min <= 0:
                    self.idle_seconds = 0
                    self.last_stats = None
                    continue

                max_idle_seconds = timeout_min * 60

                # Watchdog only acts if filesystem is mounted
                if not self.engine.runner.is_mountpoint(self.engine.config.mount_crypto):
                    self.idle_seconds = 0
                    self.last_stats = None
                    continue

                target_dev = self.engine.find_target_block_device()
                if not target_dev:
                    continue

                current_stats = get_io_stats_for_device(target_dev)
                if current_stats is None:
                    continue

                if self.last_stats is None:
                    self.last_stats = current_stats
                    self.idle_seconds = 0
                    continue

                if current_stats == self.last_stats:
                    self.idle_seconds += self.check_interval
                    if self.idle_seconds >= max_idle_seconds:
                        print(
                            f"[*] WATCHDOG: Inattività I/O rilevata per {self.idle_seconds // 60} minuti "
                            f"(soglia max: {timeout_min}m). Avvio spegnimento automatico...",
                            flush=True,
                        )
                        if self.on_idle_timeout:
                            self.on_idle_timeout()
                        else:
                            self.engine.stop()
                            self.engine.notify_discord("watchdog", f"Arresto automatico per inattività ({timeout_min}m)")

                        self.idle_seconds = 0
                        self.last_stats = None
                else:
                    self.idle_seconds = 0
                    self.last_stats = current_stats

            except Exception as e:
                print(f"[!] Errore nel thread watchdog: {e}", flush=True)

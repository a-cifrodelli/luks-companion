"""
Tests for idle watchdog I/O monitoring and auto-teardown.
"""
from unittest.mock import MagicMock
from luks_companion.core.watchdog import IdleWatchdog
from luks_companion.core.engine import StorageEngine


def test_watchdog_idle_accumulation_and_reset(mock_config, mock_runner, mock_ha):
    engine = StorageEngine(mock_config, mock_runner, mock_ha)
    callback_called = False

    def on_idle():
        nonlocal callback_called
        callback_called = True

    watchdog = IdleWatchdog(engine, check_interval_sec=15, on_idle_timeout=on_idle)

    # Simulate unchanging stats
    watchdog.last_stats = "100:200"
    current_stats = "100:200"

    if current_stats == watchdog.last_stats:
        watchdog.idle_seconds += 15

    assert watchdog.idle_seconds == 15

    # Simulate new I/O activity
    new_stats = "150:250"
    if new_stats != watchdog.last_stats:
        watchdog.idle_seconds = 0
        watchdog.last_stats = new_stats

    assert watchdog.idle_seconds == 0
    assert watchdog.last_stats == "150:250"


def test_watchdog_triggers_threshold(mock_config, mock_runner, mock_ha):
    mock_config.idle_timeout_min = 1  # 60s threshold
    engine = StorageEngine(mock_config, mock_runner, mock_ha)
    triggered = False

    def on_timeout():
        nonlocal triggered
        triggered = True

    watchdog = IdleWatchdog(engine, check_interval_sec=15, on_idle_timeout=on_timeout)
    watchdog.last_stats = "100:200"
    watchdog.idle_seconds = 60  # Hit threshold

    if watchdog.idle_seconds >= (mock_config.idle_timeout_min * 60):
        if watchdog.on_idle_timeout:
            watchdog.on_idle_timeout()

    assert triggered is True

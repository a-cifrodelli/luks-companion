"""
Tests for SMART hardware telemetry and volume stats calculations.
"""
import json
from unittest.mock import patch
from luks_companion.core.smart import get_smart_telemetry, format_bytes


def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024) == "1 KB"
    assert format_bytes(1024 * 1024 * 50) == "50 MB"
    assert format_bytes(1024 * 1024 * 1024 * 250) == "250.0 GB"
    assert format_bytes(1024 * 1024 * 1024 * 1024 * 2) == "2.0 TB"


def test_smart_telemetry_standby_0w():
    res = get_smart_telemetry(None)
    assert res["supported"] is False
    assert "0W Standby" in res["reason"]


def test_smart_telemetry_healthy_json(mock_runner):
    mock_runner.existing_paths.add("/dev/sdc")

    fake_smartctl_output = json.dumps({
        "smart_status": {"passed": True},
        "temperature": {"current": 33},
        "model_name": "WDC WD20EZAZ-00GGJB0",
        "serial_number": "WD-WCC4N7LXYZ12",
    })

    mock_runner.set_command_response("/usr/bin/smartctl", stdout=fake_smartctl_output)
    mock_runner.set_command_response("smartctl", stdout=fake_smartctl_output)

    with patch("luks_companion.core.smart.find_smartctl_bin", return_value="/usr/bin/smartctl"):
        res = get_smart_telemetry("/dev/sdc", mock_runner)
        assert res["supported"] is True
        assert res["health"] == "PASSED"
        assert res["temperature_c"] == 33
        assert res["model"] == "WDC WD20EZAZ-00GGJB0"
        assert res["serial"] == "WD-WCC4N7LXYZ12"

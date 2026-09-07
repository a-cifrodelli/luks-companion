"""
Unit tests for HomeAssistantClient (luks_companion.core.ha_client).
"""
import io
import json
import time
from unittest.mock import MagicMock, patch
import pytest

from luks_companion.core.config import Config
from luks_companion.core.ha_client import HomeAssistantClient


def test_ha_disabled():
    cfg = Config(enable_home_assistant=False)
    client = HomeAssistantClient(cfg)

    assert client.is_enabled is False
    assert client.get_state() == "always-on"
    assert client.turn_on() is True
    assert client.turn_off() is True


def test_ha_get_state_success():
    cfg = Config(
        enable_home_assistant=True,
        ha_url="http://ha.local:8123",
        ha_token="mock-token-123",
        ha_entity_id="switch.smart_plug",
    )
    client = HomeAssistantClient(cfg)

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"state": "on"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        state = client.get_state()
        assert state == "on"
        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]
        assert "switch.smart_plug" in req.full_url
        assert req.headers["Authorization"] == "Bearer mock-token-123"


def test_ha_get_state_caching():
    cfg = Config(
        enable_home_assistant=True,
        ha_url="http://ha.local:8123",
        ha_token="mock-token-123",
        ha_entity_id="switch.smart_plug",
    )
    client = HomeAssistantClient(cfg)

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"state": "on"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        # First call hits network
        state1 = client.get_state()
        assert state1 == "on"
        assert mock_open.call_count == 1

        # Second immediate call uses TTL cache (no network hit)
        state2 = client.get_state()
        assert state2 == "on"
        assert mock_open.call_count == 1

        # Force refresh bypasses cache
        state3 = client.get_state(force_refresh=True)
        assert state3 == "on"
        assert mock_open.call_count == 2


def test_ha_get_state_error_handling():
    cfg = Config(
        enable_home_assistant=True,
        ha_url="http://ha.local:8123",
        ha_token="mock-token-123",
        ha_entity_id="switch.smart_plug",
    )
    client = HomeAssistantClient(cfg)

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        state = client.get_state(force_refresh=True)
        assert state == "unknown"


def test_ha_turn_on_and_off():
    cfg = Config(
        enable_home_assistant=True,
        ha_url="http://ha.local:8123",
        ha_token="mock-token-123",
        ha_entity_id="switch.smart_plug",
    )
    client = HomeAssistantClient(cfg)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        assert client.turn_on() is True
        req_on = mock_open.call_args[0][0]
        assert "switch/turn_on" in req_on.full_url
        assert json.loads(req_on.data.decode("utf-8")) == {"entity_id": "switch.smart_plug"}

        assert client.turn_off() is True
        req_off = mock_open.call_args[0][0]
        assert "switch/turn_off" in req_off.full_url
        assert json.loads(req_off.data.decode("utf-8")) == {"entity_id": "switch.smart_plug"}


def test_ha_call_service_failure():
    cfg = Config(
        enable_home_assistant=True,
        ha_url="http://ha.local:8123",
        ha_token="mock-token-123",
        ha_entity_id="switch.smart_plug",
    )
    client = HomeAssistantClient(cfg)

    with patch("urllib.request.urlopen", side_effect=Exception("Timeout")):
        assert client.turn_on() is False

"""
Tests for Discord Webhook Notification Dispatcher (luks_companion.core.notify).
"""
import json
from unittest.mock import MagicMock, patch
import pytest

from luks_companion.core.config import Config
from luks_companion.core.notify import send_discord_notification
from luks_companion.core.engine import StorageEngine


def test_notify_discord_empty_url():
    cfg = Config(discord_webhook_url="")
    assert send_discord_notification(cfg, "test") is False


def test_notify_discord_invalid_url():
    cfg = Config(discord_webhook_url="http://not-https.com")
    assert send_discord_notification(cfg, "test") is False


def test_notify_discord_unlock_payload():
    cfg = Config(
        discord_webhook_url="https://discord.com/api/webhooks/mock/test",
        mapper_name="cryptovault",
        storage_base="/srv/storage",
    )

    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 204
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        success = send_discord_notification(cfg, "unlock")
        assert success is True

        args, kwargs = mock_open.call_args
        req = args[0]
        assert req.get_method() == "POST"
        body = json.loads(req.data.decode("utf-8"))
        embed = body["embeds"][0]
        assert "Sbloccato" in embed["title"]
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert any("Volume Group" in k and "`mybook`" in v for k, v in fields.items())
        assert any("Mount" in k and "/srv/storage/cryptovault" in v for k, v in fields.items())


def test_notify_discord_lock_payload():
    cfg = Config(
        discord_webhook_url="https://discord.com/api/webhooks/mock/test",
        vg_name="mybook",
    )

    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        success = send_discord_notification(cfg, "lock")
        assert success is True

        args, kwargs = mock_open.call_args
        req = args[0]
        body = json.loads(req.data.decode("utf-8"))
        embed = body["embeds"][0]
        assert "Sigillato" in embed["title"]
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert any("Alimentazione" in k and "0W Standby" in v for k, v in fields.items())


def test_engine_safe_teardown_triggers_lock_notification(mock_config, mock_runner, mock_ha):
    mock_config.discord_webhook_url = "https://discord.com/api/webhooks/mock/test"
    engine = StorageEngine(mock_config, runner=mock_runner, ha_client=mock_ha)

    with patch.object(engine, "notify_discord") as mock_notify:
        engine.safe_teardown()
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lock"

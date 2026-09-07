"""
Tests for Web Application HTTP Gateway (luks_companion.web.app).
Verifies IPC communication, NDJSON streaming, and API endpoints without requiring root or physical sockets.
"""
import io
import json
import base64
from unittest.mock import MagicMock, patch
import pytest

from luks_companion.web.app import send_socket_command, WebGatewayHandler
from luks_companion.core.config import Config


def test_send_socket_command_missing_socket():
    res = send_socket_command("/nonexistent/path/luks-manager.sock", {"action": "status"})
    assert res["status"] == "error"
    assert "non trovato" in res["message"]


def test_send_socket_command_success():
    fake_lines = [
        json.dumps({"event": "log", "line": "Step 1..."}) + "\n",
        json.dumps({"event": "done", "status": "ok", "message": "All good", "data": {"status": "mounted"}}) + "\n",
    ]

    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    mock_file = io.StringIO("".join(fake_lines))
    mock_sock.makefile.return_value = mock_file

    with patch("os.path.exists", return_value=True), patch("socket.socket", return_value=mock_sock):
        res = send_socket_command("/run/luks-manager.sock", {"action": "status"})
        assert res["status"] == "ok"
        assert res["message"] == "All good"
        assert res["data"]["status"] == "mounted"


def test_web_gateway_ndjson_streaming():
    cfg = Config()
    cfg.socket_path = "/run/luks-manager.sock"

    fake_lines = [
        json.dumps({"event": "log", "line": "[1/7] Accensione..."}) + "\n",
        json.dumps({"event": "done", "status": "ok", "message": "Successo"}) + "\n",
    ]

    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    mock_file = io.StringIO("".join(fake_lines))
    mock_sock.makefile.return_value = mock_file

    with patch("os.path.exists", return_value=True), patch("socket.socket", return_value=mock_sock):
        handler = WebGatewayHandler.__new__(WebGatewayHandler)
        handler.config = cfg
        handler.wfile = io.BytesIO()
        handler.headers_sent = []
        handler.response_code = None

        def fake_send_response(code):
            handler.response_code = code

        def fake_send_header(key, val):
            handler.headers_sent.append((key, val))

        def fake_end_headers():
            pass

        handler.send_response = fake_send_response
        handler.send_header = fake_send_header
        handler.end_headers = fake_end_headers

        handler.handle_streamed_socket_action({"action": "unlock"}, timeout=10)

        assert handler.response_code == 200
        headers_dict = dict(handler.headers_sent)
        assert "application/x-ndjson" in headers_dict.get("Content-Type", "")
        assert headers_dict.get("Connection") == "close"

        output = handler.wfile.getvalue().decode("utf-8")
        assert not output.startswith("data:")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        assert len(lines) == 2
        obj1 = json.loads(lines[0])
        assert obj1["event"] == "log"
        obj2 = json.loads(lines[1])
        assert obj2["event"] == "done"
        assert obj2["status"] == "ok"


def test_web_gateway_header_backup_download():
    cfg = Config()
    cfg.socket_path = "/run/luks-manager.sock"

    raw_header_bytes = b"LUKS2_HEADER_MOCK_DATA_12345"
    b64_header = base64.b64encode(raw_header_bytes).decode("ascii")

    handler = WebGatewayHandler.__new__(WebGatewayHandler)
    handler.config = cfg
    handler.path = "/api/header/backup"
    handler.wfile = io.BytesIO()
    handler.headers_sent = []
    handler.response_code = None

    def fake_send_response(code):
        handler.response_code = code

    def fake_send_header(key, val):
        handler.headers_sent.append((key, val))

    def fake_end_headers():
        pass

    handler.send_response = fake_send_response
    handler.send_header = fake_send_header
    handler.end_headers = fake_end_headers

    with patch("luks_companion.web.app.send_socket_command") as mock_send:
        mock_send.return_value = {
            "status": "ok",
            "header_base64": b64_header,
            "filename": "backup.header"
        }
        handler.do_GET()

        assert handler.response_code == 200
        headers_dict = dict(handler.headers_sent)
        assert headers_dict.get("Content-Type") == "application/octet-stream"
        assert "backup.header" in headers_dict.get("Content-Disposition", "")
        assert handler.wfile.getvalue() == raw_header_bytes

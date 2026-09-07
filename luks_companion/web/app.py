"""
Web Application HTTP Gateway (runs as unprivileged user).
Serves desktop-first frontend and relays requests to the privileged Master Daemon via IPC socket.
"""
import os
import sys
import json
import socket
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import Optional, Dict, Any

from ..core.config import Config
from ..core.privdrop import drop_privileges


def send_socket_command(sock_path: str, payload: dict, timeout: int = 45) -> dict:
    if not os.path.exists(sock_path):
        return {
            "status": "error",
            "message": f"Socket demone non trovato ({sock_path}). Verificare che luks-managerd.service sia attivo.",
        }

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(sock_path)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))

            with sock.makefile("r", encoding="utf-8", errors="replace") as f:
                last_obj = None
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            obj = json.loads(line)
                            last_obj = obj
                            if obj.get("event") == "done":
                                return obj
                        except json.JSONDecodeError:
                            pass
                if last_obj:
                    return last_obj
            return {"status": "error", "message": "Nessuna risposta valida ricevuta dal demone"}
    except Exception as e:
        return {"status": "error", "message": f"Errore comunicazione con demone: {e}"}


class WebGatewayHandler(BaseHTTPRequestHandler):
    config: Config
    static_dir: str

    def log_message(self, format, *args):
        # Quiet standard HTTP access logs
        pass

    def send_json(self, data: dict, status_code: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            res = send_socket_command(self.config.socket_path, {"action": "status"})
            self.send_json(res.get("data", res))

        elif path == "/api/diagnose":
            res = send_socket_command(self.config.socket_path, {"action": "diagnose"})
            self.send_json(res.get("data", res))

        elif path == "/api/header/backup":
            res = send_socket_command(self.config.socket_path, {"action": "header_backup"}, timeout=30)
            self.send_json(res)

        elif path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            st = send_socket_command(self.config.socket_path, {"action": "status"})
            msg = f"data: {json.dumps({'event': 'status', 'data': st.get('data', {})})}\n\n"
            try:
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        else:
            self.serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            req_data = json.loads(post_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json({"status": "error", "message": "Corpo richiesta JSON non valido"}, 400)
            return

        if path == "/api/unlock":
            self.handle_streamed_socket_action({
                "action": "unlock",
                "passphrase": req_data.get("passphrase"),
                "keyfile_base64": req_data.get("keyfile_base64"),
            }, timeout=60)

        elif path == "/api/stop":
            self.handle_streamed_socket_action({"action": "stop"}, timeout=45)

        elif path == "/api/header/restore":
            res = send_socket_command(self.config.socket_path, {
                "action": "header_restore",
                "header_base64": req_data.get("header_base64"),
            }, timeout=30)
            self.send_json(res)

        else:
            self.send_json({"status": "error", "message": "Endpoint non trovato"}, 404)

    def handle_streamed_socket_action(self, payload: dict, timeout: int = 45):
        sock_path = self.config.socket_path
        if not os.path.exists(sock_path):
            self.send_json({
                "status": "error",
                "message": f"Socket demone non trovato ({sock_path}). Verificare luks-managerd.service",
            }, 500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(sock_path)
                sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))

                with sock.makefile("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            msg = f"data: {line}\n\n"
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
        except Exception as e:
            err_obj = json.dumps({"event": "done", "status": "error", "message": str(e)})
            try:
                self.wfile.write(f"data: {err_obj}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

    def serve_static(self, path: str):
        if path == "/" or not path:
            path = "/index.html"

        safe_path = os.path.normpath(path.lstrip("/"))
        file_path = os.path.join(self.static_dir, safe_path)

        if not os.path.commonpath([self.static_dir, file_path]).startswith(self.static_dir):
            self.send_response(403)
            self.end_headers()
            return

        if not os.path.exists(file_path) or os.path.isdir(file_path):
            self.send_response(404)
            self.end_headers()
            return

        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_response(500)
            self.end_headers()


def run_web_server(config: Optional[Config] = None, drop_privs_user: Optional[str] = None) -> None:
    cfg = config or Config.from_env_file()

    # Drop privileges if requested and running as root
    if drop_privs_user:
        try:
            if drop_privileges(drop_privs_user, cfg.socket_group):
                print(f"[*] Privilege dropping eseguito con successo per l'utente: {drop_privs_user}")
        except Exception as e:
            print(f"[!] Errore durante il privilege dropping: {e}", file=sys.stderr)

    # Static directory resolution
    static_dir = os.path.join(cfg.base_dir, "web", "static")
    if not os.path.exists(static_dir):
        # Fallback to current relative path
        static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web", "static")

    WebGatewayHandler.config = cfg
    WebGatewayHandler.static_dir = static_dir

    server = HTTPServer((cfg.web_host, cfg.web_port), WebGatewayHandler)
    print(f"[*] LUKS-Companion Web Gateway attivo su http://{cfg.web_host}:{cfg.web_port}")
    print(f"[*] Serving static assets da: {static_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Arresto Web Gateway...")
        server.server_close()

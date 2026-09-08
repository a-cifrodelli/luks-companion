"""
Web Application HTTP Gateway (runs as unprivileged user).
Serves desktop-first frontend and relays requests to the privileged Master Daemon via IPC socket.
"""
import os
import sys
import json
import socket
import mimetypes
import base64
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
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
        with socket.socket(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM) as sock:
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


MOCK_SERVER_DATA = {
    "status": "mounted",
    "unlocked": True,
    "mounted": True,
    "vg_active": True,
    "plug_state": "on",
    "plug_powered": True,
    "disk_present": True,
    "target_dev": "/dev/sdb",
    "webdav_active": True,
    "webdav_port": 9088,
    "vg_name": "vg_storage",
    "mapper_name": "secure_vault",
    "mount_crypto": "/srv/storage/secure_vault",
    "mount_backup": "/srv/storage/backup_vault",
    "volumes": [
        {
            "name": "Volume Dati (secure_vault)",
            "mountpoint": "/srv/storage/secure_vault",
            "total_bytes": 1968840245248,
            "used_bytes": 247839211520,
            "free_bytes": 1721001033728,
            "used_percent": 12.6,
            "total_human": "1.8 TB",
            "used_human": "230.8 GB",
            "free_human": "1.6 TB",
        },
        {
            "name": "Volume Backup (backup_vault)",
            "mountpoint": "/srv/storage/backup_vault",
            "total_bytes": 984420122624,
            "used_bytes": 413456451502,
            "free_bytes": 570963671122,
            "used_percent": 42.0,
            "total_human": "916.8 GB",
            "used_human": "385.1 GB",
            "free_human": "531.7 GB",
        },
    ],
    "smart": {
        "supported": True,
        "installed": True,
        "device": "/dev/sdb",
        "health": "PASSED",
        "temperature_c": 36,
        "model": "Generic External Disk (USB 3.0)",
        "serial": "SN-DEMO-98765432",
    },
}


class WebGatewayHandler(BaseHTTPRequestHandler):
    config: Config
    static_dir: str
    is_mock: bool = False

    def log_message(self, format, *args):
        # Quiet standard HTTP access logs
        pass

    def send_json(self, data: dict, status_code: int = 200):
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
            if self.is_mock or "mock=1" in parsed.query:
                self.send_json({"status": "ok", "data": MOCK_SERVER_DATA})
                return
            res = send_socket_command(self.config.socket_path, {"action": "status"})
            self.send_json(res)

        elif path == "/api/diagnose":
            res = send_socket_command(self.config.socket_path, {"action": "diagnose"})
            self.send_json(res)

        elif path == "/api/header/backup":
            res = send_socket_command(self.config.socket_path, {"action": "header_backup"}, timeout=30)
            if res.get("status") == "ok" and res.get("header_base64"):
                try:
                    header_bytes = base64.b64decode(res["header_base64"])
                    filename = res.get("filename", "luks_header_backup.header")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", str(len(header_bytes)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(header_bytes)
                    return
                except Exception as e:
                    self.send_json({"status": "error", "message": f"Errore codifica download: {e}"}, 500)
                    return
            else:
                self.send_json({"status": "error", "message": res.get("message", "Errore durante il backup dell'header")}, 400)
                return

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

        elif path in ("/flowchart", "/flowchart/", "/flowchart/index.html"):
            flowchart_path = os.path.join(self.config.base_dir, "docs", "flowchart", "index.html")
            if not os.path.exists(flowchart_path):
                flowchart_path = os.path.join(self.config.base_dir, "docs", "luks_manager_flowchart.html")
            if os.path.exists(flowchart_path):
                try:
                    with open(flowchart_path, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    self.send_json({"status": "error", "message": f"Errore lettura flowchart: {e}"}, 500)
                    return
            else:
                self.send_json({"status": "error", "message": "File flowchart non trovato"}, 404)
                return

        elif path in ("/flowchart/flowchart.js", "/flowchart.js"):
            js_path = os.path.join(self.config.base_dir, "docs", "flowchart", "flowchart.js")
            if os.path.exists(js_path):
                try:
                    with open(js_path, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    self.send_json({"status": "error", "message": f"Errore lettura flowchart.js: {e}"}, 500)
                    return
            else:
                self.send_json({"status": "error", "message": "File flowchart.js non trovato"}, 404)
                return

        elif path in ("/flowchart/vis-network.min.js", "/vis-network.min.js"):
            vis_path = os.path.join(self.config.base_dir, "docs", "flowchart", "vis-network.min.js")
            if not os.path.exists(vis_path):
                vis_path = os.path.join(self.config.base_dir, "docs", "vis-network.min.js")
            if os.path.exists(vis_path):
                try:
                    with open(vis_path, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception:
                    pass
            self.serve_static(path)

        elif path in ("/flowchart.html", "/luks_manager_flowchart.html"):
            self.send_response(302)
            self.send_header("Location", "/flowchart")
            self.end_headers()
            return

        elif path in ("/demo", "/mock"):
            self.send_response(302)
            self.send_header("Location", "/?mock=1")
            self.end_headers()
            return

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
            }, timeout=75)

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

    def handle_streamed_socket_action(self, payload: dict, timeout: int = 75):
        sock_path = self.config.socket_path
        if not os.path.exists(sock_path):
            self.send_json({
                "status": "error",
                "message": f"Socket demone non trovato ({sock_path}). Verificare luks-managerd.service",
            }, 500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            with socket.socket(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(sock_path)
                sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))

                with sock.makefile("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if not line:
                            break
                        try:
                            self.wfile.write(line.encode("utf-8"))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
        except Exception as e:
            err_line = json.dumps({"event": "done", "status": "error", "message": f"Errore streaming IPC: {str(e)}"}) + "\n"
            try:
                self.wfile.write(err_line.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        finally:
            self.close_connection = True

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

            if (path in ("/", "/index.html")) and getattr(self, "is_mock", False):
                content = content.replace(b"</head>", b"<script>window.IS_MOCK_SERVER = true;</script></head>")

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass


def run_web_server(config: Optional[Config] = None, drop_privs_user: Optional[str] = None, mock: bool = False) -> None:
    if "--mock" in sys.argv or "--demo" in sys.argv:
        mock = True

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
    WebGatewayHandler.is_mock = mock

    server = ThreadingHTTPServer((cfg.web_host, cfg.web_port), WebGatewayHandler)
    server.daemon_threads = True
    print(f"[*] LUKS-Companion Web Gateway multithreading attivo su http://{cfg.web_host}:{cfg.web_port}")
    if mock:
        print("[*] 🌟 MODALITÀ DEMO / MOCK ATTIVA (per screenshot e documentazione)")
    print(f"[*] Serving static assets da: {static_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Arresto Web Gateway...")
        server.server_close()

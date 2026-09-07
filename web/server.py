#!/usr/bin/env python3
# ===================================================================
# LUKS-MANAGER WEB APP SERVER (Desktop-First HTTP Gateway)
# ===================================================================
import os
import sys
import json
import socket
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
SOCKET_PATH = "/run/luks-manager.sock"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip().strip('"').strip("'")
    return env

env_config = load_env()
PORT = int(os.environ.get("WEB_PORT") or env_config.get("WEB_PORT") or 9099)
HOST = os.environ.get("WEB_HOST") or env_config.get("WEB_HOST") or "0.0.0.0"

import base64

def send_socket_command(payload: dict, timeout: int = 45) -> dict:
    if not os.path.exists(SOCKET_PATH):
        return {"status": "error", "message": "Socket demone non trovato (/run/luks-manager.sock). Verificare che luks-managerd.service sia attivo."}
    
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
            sock.sendall((json.dumps(payload) + "\n").encode('utf-8'))
            
            with sock.makefile('r', encoding='utf-8', errors='replace') as f:
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
        return {"status": "error", "message": f"Errore IPC demone: {str(e)}"}

class WebAppHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy standard request logging
        pass

    def send_json(self, data: dict, status_code: int = 200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def stream_socket_action(self, payload: dict, timeout: int = 60):
        if not os.path.exists(SOCKET_PATH):
            self.send_json({"event": "done", "status": "error", "message": "Demone non attivo"}, 503)
            return

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(SOCKET_PATH)
                sock.sendall((json.dumps(payload) + "\n").encode('utf-8'))

                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()

                with sock.makefile('r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        if not line:
                            break
                        try:
                            self.wfile.write(line.encode('utf-8'))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
        except Exception as e:
            try:
                err_line = json.dumps({"event": "done", "status": "error", "message": f"Errore streaming IPC: {str(e)}"}) + "\n"
                self.wfile.write(err_line.encode('utf-8'))
                self.wfile.flush()
            except Exception:
                pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            res = send_socket_command({"action": "status"}, timeout=10)
            self.send_json(res)
            return

        elif path == "/api/header/backup":
            res = send_socket_command({"action": "header_backup"}, timeout=30)
            if res.get("status") == "ok" and res.get("header_base64"):
                try:
                    header_bytes = base64.b64decode(res["header_base64"])
                    filename = res.get("filename", "luks_header_backup.header")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", str(len(header_bytes)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(header_bytes)
                    return
                except Exception as e:
                    self.send_json({"status": "error", "message": f"Errore codifica download: {e}"}, 500)
                    return
            else:
                self.send_json({"status": "error", "message": res.get("message", "Errore durante il backup dell'header")}, 400)
                return

        # Serve static assets
        if path == "/" or path == "":
            path = "/index.html"
        elif path == "/favicon.ico":
            path = "/favicon.svg"

        file_path = os.path.abspath(os.path.join(STATIC_DIR, path.lstrip("/")))

        # Security check: prevent directory traversal
        if not file_path.startswith(STATIC_DIR) or not os.path.exists(file_path) or os.path.isdir(file_path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return

        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 32 * 1024 * 1024:  # Max 32MB (fits LUKS2 16MB headers)
            self.send_json({"status": "error", "message": "Payload troppo grande"}, 413)
            return

        body = self.rfile.read(content_length)
        try:
            req_data = json.loads(body.decode('utf-8')) if body else {}
        except json.JSONDecodeError:
            self.send_json({"status": "error", "message": "JSON non valido"}, 400)
            return

        if path == "/api/unlock":
            self.stream_socket_action({
                "action": "unlock",
                "passphrase": req_data.get("passphrase", ""),
                "keyfile_base64": req_data.get("keyfile_base64", "")
            }, timeout=75)

        elif path == "/api/stop":
            self.stream_socket_action({"action": "stop"}, timeout=45)

        elif path == "/api/header/restore":
            res = send_socket_command({
                "action": "header_restore",
                "header_base64": req_data.get("header_base64", "")
            }, timeout=30)
            self.send_json(res)

        else:
            self.send_json({"status": "error", "message": "Endpoint non trovato"}, 404)

def main():
    server = HTTPServer((HOST, PORT), WebAppHandler)
    print(f"[*] LUKS Manager Web App attiva su http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Arresto Web App...")
        server.server_close()

if __name__ == "__main__":
    main()

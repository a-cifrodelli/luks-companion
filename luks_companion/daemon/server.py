"""
Master IPC Socket Server (runs as root).
Owns /run/luks-manager.sock with strict 0660 root:luks-web permissions.
Serializes operations, streams real-time execution logs, and hosts the idle watchdog.
"""
import os
import sys
import json
import socket
import signal
import base64
import threading
from typing import Dict, Any, Optional

from ..core.config import Config
from ..core.engine import StorageEngine, StorageEngineError
from ..core.runner import SubprocessRunner
from ..core.watchdog import IdleWatchdog
from ..core.diagnose import run_full_diagnosis
from ..core.privdrop import resolve_user_group


class SocketDaemon:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.from_env_file()
        self.runner = SubprocessRunner()
        self.engine = StorageEngine(self.config, self.runner)
        self.action_lock = threading.Lock()
        self.server_sock: Optional[socket.socket] = None
        self.watchdog: Optional[IdleWatchdog] = None
        self.running = True

    def _send_response(self, conn: socket.socket, payload: Dict[str, Any]) -> None:
        try:
            data = (json.dumps(payload) + "\n").encode("utf-8")
            conn.sendall(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            data = conn.recv(65536)
            if not data:
                return

            try:
                req = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_response(conn, {"event": "done", "status": "error", "message": "Payload JSON non valido"})
                return

            action = req.get("action", "")

            # 1. STATUS
            if action == "status":
                status_data = self.engine.get_status()
                status_data["busy"] = self.action_lock.locked()
                self._send_response(conn, {"event": "done", "status": "ok", "data": status_data})

            # 2. DIAGNOSE
            elif action == "diagnose":
                diag_data = run_full_diagnosis(self.config, self.runner)
                self._send_response(conn, {"event": "done", "status": "ok", "data": diag_data})

            # 3. UNLOCK / START
            elif action == "unlock" or action == "start":
                if not self.action_lock.acquire(blocking=False):
                    self._send_response(
                        conn,
                        {"event": "done", "status": "busy", "message": "Un'altra operazione è già in corso sul disco..."},
                    )
                    return

                try:
                    key_bytes = None
                    passphrase = None

                    if req.get("keyfile_base64"):
                        try:
                            key_bytes = base64.b64decode(req["keyfile_base64"])
                        except Exception as e:
                            self._send_response(
                                conn, {"event": "done", "status": "error", "message": f"Keyfile base64 non valido: {e}"}
                            )
                            return
                    elif req.get("passphrase"):
                        passphrase = req["passphrase"]

                    if not key_bytes and not passphrase:
                        self._send_response(
                            conn,
                            {
                                "event": "done",
                                "status": "error",
                                "message": "Nessuna credenziale (passphrase o keyfile) fornita.",
                            },
                        )
                        return

                    log_lines = []

                    def log_emitter(line: str) -> None:
                        log_lines.append(line)
                        self._send_response(
                            conn,
                            {
                                "event": "log",
                                "line": line,
                                "data": self.engine.get_status(),
                            },
                        )

                    try:
                        self.engine.start(
                            passphrase=passphrase,
                            keyfile_bytes=key_bytes,
                            log_cb=log_emitter,
                        )
                        del key_bytes
                        del passphrase
                        self._send_response(
                            conn,
                            {
                                "event": "done",
                                "status": "ok",
                                "message": "Volume sbloccato e montato con successo",
                                "output": "\n".join(log_lines),
                                "data": self.engine.get_status(),
                            },
                        )
                    except Exception as ex:
                        err_msg = str(ex)
                        hint = getattr(ex, "hint", "")
                        self._send_response(
                            conn,
                            {
                                "event": "done",
                                "status": "error",
                                "message": err_msg,
                                "hint": hint,
                                "output": "\n".join(log_lines),
                                "data": self.engine.get_status(),
                            },
                        )
                finally:
                    self.action_lock.release()

            # 4. STOP / LOCK
            elif action == "stop" or action == "lock":
                if not self.action_lock.acquire(blocking=False):
                    self._send_response(
                        conn,
                        {"event": "done", "status": "busy", "message": "Operazione di arresto già in corso..."},
                    )
                    return

                try:
                    log_lines = []

                    def log_emitter(line: str) -> None:
                        log_lines.append(line)
                        self._send_response(
                            conn,
                            {
                                "event": "log",
                                "line": line,
                                "data": self.engine.get_status(),
                            },
                        )

                    try:
                        self.engine.stop(log_cb=log_emitter)
                        self._send_response(
                            conn,
                            {
                                "event": "done",
                                "status": "ok",
                                "message": "Procedura di teardown e spegnimento completata con successo",
                                "output": "\n".join(log_lines),
                                "data": self.engine.get_status(),
                            },
                        )
                    except Exception as ex:
                        self._send_response(
                            conn,
                            {
                                "event": "done",
                                "status": "error",
                                "message": str(ex),
                                "output": "\n".join(log_lines),
                                "data": self.engine.get_status(),
                            },
                        )
                finally:
                    self.action_lock.release()

            # 5. HEADER BACKUP
            elif action == "header_backup":
                try:
                    res = self.engine.backup_luks_header()
                    self._send_response(conn, {"event": "done", "status": "ok", **res})
                except Exception as ex:
                    self._send_response(conn, {"event": "done", "status": "error", "message": str(ex)})

            # 6. HEADER RESTORE
            elif action == "header_restore":
                if not self.action_lock.acquire(blocking=False):
                    self._send_response(
                        conn, {"event": "done", "status": "busy", "message": "Un'altra operazione è in corso sul disco..."}
                    )
                    return
                try:
                    hdr_b64 = req.get("header_base64", "")
                    if not hdr_b64:
                        self._send_response(
                            conn, {"event": "done", "status": "error", "message": "Nessun dato header fornito."}
                        )
                        return
                    try:
                        hdr_bytes = base64.b64decode(hdr_b64)
                    except Exception as e:
                        self._send_response(
                            conn, {"event": "done", "status": "error", "message": f"Decodifica header fallita: {e}"}
                        )
                        return

                    self.engine.restore_luks_header(hdr_bytes)
                    self._send_response(
                        conn, {"event": "done", "status": "ok", "message": "Header LUKS2 ripristinato con successo!"}
                    )
                except Exception as ex:
                    self._send_response(conn, {"event": "done", "status": "error", "message": str(ex)})
                finally:
                    self.action_lock.release()

            else:
                self._send_response(conn, {"event": "done", "status": "error", "message": f"Azione '{action}' sconosciuta"})

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send_response(conn, {"event": "done", "status": "error", "message": f"Errore interno demone: {e}"})
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def start_server(self) -> None:
        sock_path = self.config.socket_path
        if os.path.exists(sock_path):
            try:
                os.remove(sock_path)
            except OSError:
                pass

        os.makedirs(os.path.dirname(sock_path), exist_ok=True)
        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(sock_path)

        # Apply strict permissions: 0660 (root:luks-web)
        try:
            _, group_gid = resolve_user_group("root", self.config.socket_group)
            os.chown(sock_path, 0, group_gid)
            os.chmod(sock_path, 0o660)
        except Exception as e:
            # Fallback for systems without luks-web group or development environments
            try:
                os.chmod(sock_path, 0o666)
            except Exception:
                pass

        self.server_sock.listen(15)

        def shutdown_handler(signum, frame):
            self.running = False
            if self.watchdog:
                self.watchdog.stop_watchdog()
            if self.server_sock:
                self.server_sock.close()
            if os.path.exists(sock_path):
                try:
                    os.remove(sock_path)
                except OSError:
                    pass
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        # Start background Idle Watchdog
        self.watchdog = IdleWatchdog(self.engine)
        self.watchdog.start()

        print(f"[*] LUKS-Companion Master Daemon attivo su: {sock_path}")
        print(f"[*] Permessi socket: 0660 (Gruppo autorizzato: {self.config.socket_group})")

        while self.running:
            try:
                conn, _ = self.server_sock.accept()
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except (BrokenPipeError, ConnectionResetError, OSError):
                if not self.running:
                    break
            except Exception as e:
                print(f"[!] Errore connessione client: {e}", file=sys.stderr)


def run_daemon(config_path: Optional[str] = None) -> None:
    cfg = Config.from_env_file(config_path)
    daemon = SocketDaemon(cfg)
    daemon.start_server()

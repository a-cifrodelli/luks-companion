"""
System execution runner and structured exception models.
Eliminates opaque error handling with actionable remediation hints.
"""
from dataclasses import dataclass, field
import subprocess
import time
import os
import sys
from typing import List, Optional, Callable, Dict, Any, Union


@dataclass
class CommandResult:
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class StorageEngineError(Exception):
    """
    Structured storage exception.
    Contains full operational context, command outputs, and remediation hints.
    """
    def __init__(
        self,
        phase: str,
        message: str,
        command: Optional[Union[List[str], str]] = None,
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
        hint: str = "",
    ):
        super().__init__(message)
        self.phase = phase
        self.message = message
        self.command = command
        self.returncode = returncode
        self.stdout = stdout.strip()
        self.stderr = stderr.strip()
        self.hint = hint.strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "error": self.message,
            "command": " ".join(self.command) if isinstance(self.command, list) else self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "hint": self.hint,
        }

    def format_detailed(self) -> str:
        lines = [
            f"[!] ERRORE NELLA FASE [{self.phase}]: {self.message}",
        ]
        if self.command:
            cmd_str = " ".join(self.command) if isinstance(self.command, list) else str(self.command)
            lines.append(f"    Comando:    {cmd_str}")
        if self.returncode is not None:
            lines.append(f"    Exit Code:  {self.returncode}")
        if self.stderr:
            lines.append(f"    Dettagli:   {self.stderr}")
        elif self.stdout:
            lines.append(f"    Output:     {self.stdout}")
        if self.hint:
            lines.append(f"    👉 CONSIGLIO: {self.hint}")
        return "\n".join(lines)


class SystemRunner:
    """
    Interface for running system commands, file existence checks, and sync.
    Can be replaced or mocked 100% in Pytest suites without physical hardware.
    """
    def run(
        self,
        cmd: List[str],
        timeout: Optional[float] = None,
        stdin_data: Optional[bytes] = None,
        check: bool = False,
        phase: str = "SYSTEM",
        hint: str = "",
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        raise NotImplementedError

    def path_exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def is_block_device(self, path: str) -> bool:
        import stat
        if not os.path.exists(path):
            return False
        return stat.S_ISBLK(os.stat(path).st_mode)

    def is_mountpoint(self, path: str) -> bool:
        return os.path.ismount(path)

    def sync(self) -> None:
        if hasattr(os, "sync"):
            os.sync()


class SubprocessRunner(SystemRunner):
    """
    Standard production runner using subprocess.
    """
    def run(
        self,
        cmd: List[str],
        timeout: Optional[float] = None,
        stdin_data: Optional[bytes] = None,
        check: bool = False,
        phase: str = "SYSTEM",
        hint: str = "",
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        t0 = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = proc.communicate(input=stdin_data, timeout=timeout)
            duration = time.time() - t0

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")
            ret = proc.returncode

            result = CommandResult(
                cmd=cmd,
                returncode=ret,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_sec=duration,
            )

            if log_callback and stdout_str:
                for line in stdout_str.splitlines():
                    if line.strip():
                        log_callback(line)

            if check and ret != 0:
                raise StorageEngineError(
                    phase=phase,
                    message=f"Comando '{cmd[0]}' fallito con codice {ret}",
                    command=cmd,
                    returncode=ret,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    hint=hint,
                )

            return result

        except subprocess.TimeoutExpired as te:
            duration = time.time() - t0
            proc.kill()
            err_msg = f"Timeout ({timeout}s) superato durante l'esecuzione del comando"
            if check:
                raise StorageEngineError(
                    phase=phase,
                    message=err_msg,
                    command=cmd,
                    returncode=-1,
                    hint=hint or "Il dispositivo o il servizio non ha risposto entro il tempo limite.",
                ) from te
            return CommandResult(
                cmd=cmd,
                returncode=-1,
                stdout="",
                stderr=err_msg,
                duration_sec=duration,
            )

        except FileNotFoundError as fe:
            duration = time.time() - t0
            err_msg = f"Eseguibile non trovato nel sistema: {cmd[0]}"
            if check:
                raise StorageEngineError(
                    phase=phase,
                    message=err_msg,
                    command=cmd,
                    returncode=127,
                    hint=f"Assicurarsi che il pacchetto contenente '{cmd[0]}' sia installato.",
                ) from fe
            return CommandResult(
                cmd=cmd,
                returncode=127,
                stdout="",
                stderr=err_msg,
                duration_sec=duration,
            )

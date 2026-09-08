"""Sichere Subprozess-Ausführung. Ausschließlich Argumentlisten, niemals
``shell=True``. Benutzereingaben werden nie an eine Shell übergeben."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass


class ToolNotFoundError(RuntimeError):
    def __init__(self, tool: str) -> None:
        super().__init__(
            f"Benötigtes Programm '{tool}' wurde nicht gefunden. "
            f"Bitte installieren (siehe README / install.sh)."
        )
        self.tool = tool


def ensure_tool(binary: str) -> str:
    """Prüft, ob ein Programm im PATH vorhanden ist, und gibt den Pfad zurück."""
    path = shutil.which(binary)
    if not path:
        raise ToolNotFoundError(binary)
    return path


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


def run_sync(args: list[str], timeout: float | None = None) -> ProcResult:
    """Synchroner Aufruf (für Worker/Skripte). ``shell=False`` per Konstruktion."""
    ensure_tool(args[0])
    try:
        proc = subprocess.run(  # noqa: S603 - Argumentliste, kein shell
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout
        partial = raw.decode(errors="replace") if isinstance(raw, bytes) else (raw or "")
        return ProcResult(returncode=124, stdout=partial, stderr="Timeout")
    return ProcResult(proc.returncode, proc.stdout, proc.stderr)


async def run_async(args: list[str], timeout: float | None = None) -> ProcResult:
    """Asynchroner Aufruf für das Event-Loop des API-Prozesses."""
    ensure_tool(args[0])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ProcResult(returncode=124, stdout="", stderr="Timeout")
    return ProcResult(
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode(errors="replace"),
        stderr_b.decode(errors="replace"),
    )

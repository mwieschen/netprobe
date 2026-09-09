"""Sichere Subprozess-Ausführung. Ausschließlich Argumentlisten, niemals
``shell=True``. Benutzereingaben werden nie an eine Shell übergeben."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass

# Erlaubte Zeichen für einen Host/IP-Parameter, der an ein externes Programm oder
# einen Socket übergeben wird. Bewusst restriktiv (Buchstaben, Ziffern, ".", "-",
# "_", ":" für IPv6). Ein führendes "-" wird zusätzlich unten abgewiesen, damit die
# Eingabe niemals als Kommandozeilen-Flag interpretiert werden kann.
_NETWORK_TARGET_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


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


def validate_network_target(target: str) -> str:
    """Validiert einen vom Benutzer übergebenen Host/IP-Parameter.

    Verhindert Argument-Injection (führendes ``-``) und begrenzt den Zeichensatz.
    Gibt den bereinigten Wert zurück oder wirft ``ValueError``. Zentral, damit alle
    neuen Dienste (VoIP, Traceroute, Health-Checks) dieselbe Prüfung verwenden.
    """
    target = target.strip()
    if target.startswith("-") or not _NETWORK_TARGET_RE.match(target):
        raise ValueError(f"Ungültiger Host: {target!r}")
    return target


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
            errors="replace",  # robuste Dekodierung (Tools mit Nicht-UTF-8-Ausgabe)
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

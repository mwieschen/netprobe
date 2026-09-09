"""Langzeit-Captures mit Ringpuffer.

``dumpcap`` läuft dauerhaft als Hintergrund-Subprozess und rotiert die PCAPNG-
Dateien selbst (``-b filesize`` / ``-b files``). Der :class:`LongCaptureManager`
verwaltet je Job genau einen Prozess und überwacht dessen Lebensdauer.

Sicherheit:
- Ausschließlich Argumentliste, niemals ``shell=True``.
- Interface-Name und BPF-Filter werden über :mod:`app.services.capture_service`
  validiert.
- ``dumpcap`` erhält seine Capabilities (``CAP_NET_RAW``) über den systemd-Dienst
  (``AmbientCapabilities``); es wird **nicht** als root ausgeführt.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.db.session import session_scope
from app.models.capture_job import CaptureJob
from app.services.capture_service import validate_bpf, validate_iface
from app.services.proc import ToolNotFoundError, ensure_tool

log = get_logger("netprobe.longcapture")


def job_dir(job_id: int) -> Path:
    return settings.capture_dir / str(job_id)


def clamp_job_limits(max_file_size_mb: int, max_files: int) -> tuple[int, int]:
    size = max(1, min(int(max_file_size_mb), settings.capture_job_max_filesize_mb))
    files = max(1, min(int(max_files), settings.capture_job_max_files))
    if size * files > settings.capture_job_max_total_mb:
        # Anzahl Dateien so weit reduzieren, dass das Gesamtlimit eingehalten wird.
        files = max(1, settings.capture_job_max_total_mb // size)
    return size, files


def build_rotating_capture_args(
    iface: str, out_path: Path, max_file_size_mb: int, max_files: int, bpf: str | None
) -> list[str]:
    """``dumpcap -i <iface> -w <out> -b filesize:<kib> -b files:<n> [-f <bpf>]``."""
    args = [
        settings.dumpcap_bin,
        "-q",  # keine fortlaufende Paketzähler-Ausgabe (sonst füllt sich die stderr-Pipe)
        "-i",
        validate_iface(iface),
        "-w",
        str(out_path),
        "-b",
        f"filesize:{max_file_size_mb * 1024}",
        "-b",
        f"files:{max_files}",
        # Ohne -a läuft dumpcap unbegrenzt weiter, bis es beendet wird.
    ]
    bpf = validate_bpf(bpf)
    if bpf:
        args += ["-f", bpf]
    return args


class LongCaptureManager:
    def __init__(self) -> None:
        self._procs: dict[int, asyncio.subprocess.Process] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def is_running(self, job_id: int) -> bool:
        proc = self._procs.get(job_id)
        return proc is not None and proc.returncode is None

    async def start(self, job_id: int) -> None:
        if self.is_running(job_id):
            return

        with session_scope() as db:
            job = db.get(CaptureJob, job_id)
            if job is None:
                return
            iface, bpf = job.iface, job.bpf
            size_mb, files = job.max_file_size_mb, job.max_files

        directory = job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o2770)

        try:
            args = build_rotating_capture_args(
                iface, directory / "cap.pcapng", size_mb, files, bpf
            )
            ensure_tool(args[0])
        except (ToolNotFoundError, ValueError) as exc:
            self._mark_error(job_id, str(exc))
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:  # pragma: no cover - Startfehler
            self._mark_error(job_id, str(exc))
            return

        self._procs[job_id] = proc
        log.info("Langzeit-Capture %s gestartet (%s, %d×%d MiB)", job_id, iface, files, size_mb)
        asyncio.ensure_future(self._watch(job_id, proc))

    async def _watch(self, job_id: int, proc: asyncio.subprocess.Process) -> None:
        stderr_b = await proc.stderr.read() if proc.stderr else b""
        rc = await proc.wait()
        # Nur aufräumen, wenn es noch derselbe Prozess ist.
        if self._procs.get(job_id) is proc:
            self._procs.pop(job_id, None)

        with session_scope() as db:
            job = db.get(CaptureJob, job_id)
            if job is None or job.status != "running":
                return  # regulär gestoppt (Endpoint hat status bereits gesetzt)
            # dumpcap wurde nicht von uns beendet -> unerwartetes Ende.
            msg = stderr_b.decode(errors="replace").strip().splitlines()
            tail = msg[-1] if msg else f"dumpcap beendet (Exit-Code {rc})"
            job.status = "error"
            job.error = tail
            job.stopped_at = utcnow()
            log.warning("Langzeit-Capture %s unerwartet beendet: %s", job_id, tail)

    async def stop(self, job_id: int) -> None:
        proc = self._procs.pop(job_id, None)
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        log.info("Langzeit-Capture %s gestoppt", job_id)

    async def shutdown(self) -> None:
        for job_id in list(self._procs):
            await self.stop(job_id)

    def resume_all(self) -> None:
        """Beim Start alle als ``running`` markierten Jobs wieder aufnehmen."""
        with session_scope() as db:
            ids = [j.id for j in db.query(CaptureJob).filter(CaptureJob.status == "running").all()]
        for job_id in ids:
            asyncio.ensure_future(self.start(job_id))

    def _mark_error(self, job_id: int, message: str) -> None:
        with session_scope() as db:
            job = db.get(CaptureJob, job_id)
            if job is not None:
                job.status = "error"
                job.error = message
                job.stopped_at = utcnow()
        log.error("Langzeit-Capture %s: %s", job_id, message)


def list_job_files(job_id: int) -> list[dict]:
    """PCAPNG-Dateien eines Jobs (aus dem Verzeichnis, nicht aus der DB)."""
    directory = job_dir(job_id)
    if not directory.is_dir():
        return []
    files: list[dict] = []
    for p in sorted(directory.glob("*.pcapng*")):
        if not p.is_file():
            continue
        st = p.stat()
        files.append(
            {
                "name": p.name,
                "size_bytes": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            }
        )
    files.sort(key=lambda f: f["mtime"])
    return files


def resolve_job_file(job_id: int, name: str) -> Path | None:
    """Validiert ``name`` und gibt den Pfad zurück, sofern er im Job-Verzeichnis liegt."""
    directory = job_dir(job_id).resolve()
    candidate = (directory / name).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def delete_job_dir(job_id: int) -> None:
    directory = job_dir(job_id)
    try:
        directory.resolve().relative_to(settings.capture_dir.resolve())
    except ValueError:  # pragma: no cover - Sicherheitsnetz
        return
    shutil.rmtree(directory, ignore_errors=True)


long_capture_manager = LongCaptureManager()

"""Report-Endpunkte: erstellen, Metadaten abrufen, HTML/PDF herunterladen."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.db.session import get_db, session_scope
from app.models.report import Report
from app.models.user import User
from app.schemas.schemas import ReportCreate, ReportOut, ReportPreview
from app.services import audit, report_service
from app.services.report_service import ReportError, report_paths

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _json_or(default, raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _to_out(report: Report) -> ReportOut:
    data = _json_or({}, report.data_json)
    return ReportOut(
        id=report.id,
        ts=report.ts,
        title=report.title,
        capture_id=report.capture_id,
        job_id=report.job_id,
        customer=report.customer,
        location=report.location,
        notes=report.notes,
        sections=_json_or([], report.sections),
        has_pdf=report.has_pdf,
        error=report.error,
        html_url=f"/api/reports/{report.id}/download?fmt=html",
        pdf_url=f"/api/reports/{report.id}/download?fmt=pdf" if report.has_pdf else None,
        key_metrics=data.get("key_metrics", []),
        interpretations=data.get("interpretations", []),
    )


@router.get("", response_model=list[ReportOut])
def list_reports(
    limit: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[ReportOut]:
    rows = db.execute(
        select(Report).order_by(Report.ts.desc()).limit(min(limit, 500))
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("/preview", response_model=ReportPreview)
async def preview_report(
    payload: ReportCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ReportPreview:
    """Key Metrics + Interpretationen ohne Datei-/DB-Erzeugung (führt die passive
    Analyse aus, falls ausgewählt – kann daher einige Sekunden dauern)."""
    if payload.capture_id is None and payload.job_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "capture_id oder job_id erforderlich.")

    def _job() -> dict:
        with session_scope() as s:
            return report_service.build_snapshot(
                s, capture_id=payload.capture_id, job_id=payload.job_id, sections=payload.sections
            )

    try:
        snap = await asyncio.to_thread(_job)
    except ReportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ReportPreview(
        source=snap["source"],
        sections=snap["sections"],
        key_metrics=snap["key_metrics"],
        interpretations=snap["interpretations"],
    )


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> ReportOut:
    if payload.capture_id is None and payload.job_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "capture_id oder job_id erforderlich.")

    ip = client_ip(request)

    def _job() -> int:
        with session_scope() as s:
            report = report_service.generate(
                s,
                capture_id=payload.capture_id,
                job_id=payload.job_id,
                customer=payload.customer.strip(),
                location=payload.location.strip(),
                notes=payload.notes,
                sections=payload.sections,
            )
            audit.record(s, "report_create", user_id=user.id, username=user.username,
                         detail=f"report={report.id}", ip=ip)
            return report.id

    try:
        report_id = await asyncio.to_thread(_job)
    except ReportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Report nicht gefunden")
    return _to_out(report)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> ReportOut:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report nicht gefunden")
    return _to_out(report)


@router.get("/{report_id}/download")
def download_report(
    report_id: int,
    fmt: str = "html",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report nicht gefunden")
    html_path, pdf_path = report_paths(report_id)
    if fmt == "pdf":
        if not report.has_pdf or not pdf_path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Kein PDF für diesen Report.")
        return FileResponse(
            pdf_path, media_type="application/pdf", filename=f"netprobe-report-{report_id}.pdf"
        )
    if not html_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report-Datei fehlt.")
    # HTML inline ausliefern, damit es im Browser geöffnet/gedruckt werden kann.
    return FileResponse(
        html_path, media_type="text/html; charset=utf-8", content_disposition_type="inline"
    )


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report nicht gefunden")
    for p in report_paths(report_id):
        p.unlink(missing_ok=True)
    db.delete(report)
    audit.record(db, "report_delete", user_id=user.id, username=user.username, detail=str(report_id))
    db.commit()

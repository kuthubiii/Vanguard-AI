"""Local LM Studio reasoning APIs backed only by persisted analysis evidence."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import Session

from aai.core.config import settings
from aai.db.models import AIReport, AnalysisJob
from aai.db.session import get_db
from aai.schemas.reasoning import AIReportRead, AIStatusRead, AskAIRequest, AskAIResponse
from aai.services.evidence_builder import build_analysis_evidence
from aai.services.lm_studio_client import (
    LMStudioConnectionError, LMStudioEmptyResponse, LMStudioError, LMStudioInvalidResponse,
    LMStudioRequestError, LMStudioTimeout, get_status,
)
from aai.services.reasoning_service import AIReasoningError, REPORT_LOCK as _REPORT_LOCK, answer_question, generate_report
from aai.services.activity_logger import record_activity

router = APIRouter(tags=["local AI reasoning"])
logger = logging.getLogger(__name__)


def _report_read(report: AIReport) -> AIReportRead:
    return AIReportRead(
        analysis_job_id=report.job_id,
        generated_by=report.model_name,
        generated_at=report.generated_at,
        summary=report.summary,
        observations=report.observations,
        recommendations=report.recommendations,
        limitations=report.limitations,
        evidence=report.evidence,
    )


def _get_completed_job(db: Session, job_id: str) -> AnalysisJob:
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "analysis_job_not_found", "message": "Analysis job not found."})
    if job.status != "completed":
        raise HTTPException(409, detail={"code": "analysis_not_complete", "message": "AI reasoning requires a completed analysis job."})
    return job


def _raise_reasoning_failure(error: LMStudioError | AIReasoningError) -> None:
    code = error.code
    if isinstance(error, LMStudioTimeout):
        http_status = 504
    elif isinstance(error, LMStudioConnectionError):
        http_status = 503
    elif isinstance(error, (LMStudioRequestError, LMStudioInvalidResponse, LMStudioEmptyResponse, AIReasoningError)):
        http_status = 502
    else:
        http_status = 503
    raise HTTPException(http_status, detail={"code": code, "message": str(error)}) from error


@router.get("/ai/status", response_model=AIStatusRead)
async def ai_status() -> AIStatusRead:
    return AIStatusRead(**await get_status())


@router.post("/analysis-jobs/{job_id}/ai-summary", response_model=AIReportRead)
async def create_ai_summary(
    job_id: str,
    regenerate: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> AIReportRead:
    _get_completed_job(db, job_id)
    async with _REPORT_LOCK:
        existing = db.scalar(select(AIReport).where(AIReport.job_id == job_id))
        if existing is not None and not regenerate:
            return _report_read(existing)
        request_id = str(uuid.uuid4())
        job = db.get(AnalysisJob, job_id)
        record_activity(category="REASONING", event_type="reasoning.report.requested",
                        title="AI report requested", description="A report generation request was accepted for a completed analysis.",
                        severity="INFO", status="RUNNING", source="reasoning_api",
                        analysis_job_id=job_id, video_id=job.video_id if job else None,
                        metadata={"regenerate": regenerate}, idempotency_key=f"reasoning-report:{request_id}:requested", bind=db.get_bind())
        try:
            evidence = build_analysis_evidence(db, job)
        except Exception as error:
            record_activity(category="REASONING", event_type="reasoning.evidence.failed",
                            title="Reasoning evidence preparation failed", description="Stored analysis evidence could not be prepared for the request.",
                            severity="ERROR", status="FAILED", source="reasoning_api",
                            analysis_job_id=job_id, video_id=job.video_id if job else None,
                            error_type=type(error).__name__, idempotency_key=f"reasoning-report:{request_id}:evidence-failed", bind=db.get_bind())
            raise
        record_activity(category="REASONING", event_type="reasoning.evidence.prepared",
                        title="Reasoning evidence prepared", description="Bounded evidence was assembled from persisted analysis records.",
                        severity="INFO", status="COMPLETED", source="reasoning_api",
                        analysis_job_id=job_id, video_id=job.video_id,
                        metadata={"selected_detections": len(evidence.get("representative_detections", [])),
                                  "selected_tracks": len(evidence.get("tracks", []))},
                        idempotency_key=f"reasoning-report:{request_id}:evidence-prepared", bind=db.get_bind())
        try:
            payload = await generate_report(evidence, activity_bind=db.get_bind())
        except (LMStudioError, AIReasoningError) as error:
            record_activity(category="REASONING", event_type="reasoning.report.failed",
                            title="AI report generation failed", description="The report was not validated and persisted.",
                            severity="ERROR", status="FAILED", source="reasoning_api",
                            analysis_job_id=job_id, video_id=job.video_id,
                            error_type=type(error).__name__, metadata={"error_code": error.code},
                            idempotency_key=f"reasoning-report:{request_id}:failed", bind=db.get_bind())
            _raise_reasoning_failure(error)
        persistence_started = perf_counter()
        try:
            report = existing or AIReport(report_id=str(uuid.uuid4()), job_id=job_id)
            report.model_name = settings.lm_studio_model
            report.summary = payload.summary
            report.observations = [item.model_dump(mode="json") for item in payload.observations]
            report.recommendations = [item.model_dump(mode="json") for item in payload.recommendations]
            report.limitations = payload.limitations
            report.evidence = evidence
            report.generated_at = datetime.now(timezone.utc)
            if existing is None:
                db.add(report)
            db.commit()
            db.refresh(report)
            logger.info("AI_REASONING_PERF stage=report_persistence outcome=success job_id=%s report_id=%s duration_ms=%.1f",
                        job_id, report.report_id, (perf_counter() - persistence_started) * 1000)
            return _report_read(report)
        except IntegrityError:
            db.rollback()
            saved = db.scalar(select(AIReport).where(AIReport.job_id == job_id))
            if saved is not None:
                logger.info("AI_REASONING_PERF stage=report_persistence outcome=existing_report job_id=%s report_id=%s",
                            job_id, saved.report_id)
                return _report_read(saved)
            logger.error("AI_REASONING_PERF stage=report_persistence outcome=failed job_id=%s exception_class=IntegrityError",
                         job_id)
            raise HTTPException(500, detail={"code": "ai_report_save_failed", "message": "The generated report could not be saved."})
        except SQLAlchemyError as error:
            db.rollback()
            logger.error("AI_REASONING_PERF stage=report_persistence outcome=failed job_id=%s exception_class=%s",
                         job_id, type(error).__name__)
            raise HTTPException(500, detail={"code": "ai_report_save_failed", "message": "The generated report could not be saved."}) from error


@router.get("/analysis-jobs/{job_id}/ai-summary", response_model=AIReportRead)
def get_ai_summary(job_id: str, db: Session = Depends(get_db)) -> AIReportRead:
    _get_completed_job(db, job_id)
    report = db.scalar(select(AIReport).where(AIReport.job_id == job_id))
    if report is None:
        raise HTTPException(404, detail={"code": "ai_summary_not_found", "message": "No AI summary has been generated for this analysis."})
    return _report_read(report)


@router.get("/analysis-jobs/{job_id}/ai-insights", response_model=AIReportRead)
def get_ai_insights(job_id: str, db: Session = Depends(get_db)) -> AIReportRead:
    return get_ai_summary(job_id, db)


@router.post("/analysis-jobs/{job_id}/ask-ai", response_model=AskAIResponse)
async def ask_ai(job_id: str, request: AskAIRequest, db: Session = Depends(get_db)) -> AskAIResponse:
    job = _get_completed_job(db, job_id)
    request_id = str(uuid.uuid4())
    record_activity(category="REASONING", event_type="reasoning.question.requested",
                    title="AI question submitted", description="A question was submitted for evidence-grounded local reasoning.",
                    severity="INFO", status="RUNNING", source="reasoning_api",
                    analysis_job_id=job_id, video_id=job.video_id,
                    idempotency_key=f"reasoning-question:{request_id}:requested", bind=db.get_bind())
    try:
        evidence = build_analysis_evidence(db, job)
    except Exception as error:
        record_activity(category="REASONING", event_type="reasoning.evidence.failed",
                        title="Reasoning evidence preparation failed", description="Stored analysis evidence could not be prepared for the question.",
                        severity="ERROR", status="FAILED", source="reasoning_api", analysis_job_id=job_id,
                        video_id=job.video_id, error_type=type(error).__name__,
                        idempotency_key=f"reasoning-question:{request_id}:evidence-failed", bind=db.get_bind())
        raise
    record_activity(category="REASONING", event_type="reasoning.evidence.prepared",
                    title="Reasoning evidence prepared", description="Bounded evidence was assembled from persisted analysis records.",
                    severity="INFO", status="COMPLETED", source="reasoning_api",
                    analysis_job_id=job_id, video_id=job.video_id,
                    metadata={"selected_detections": len(evidence.get("representative_detections", [])),
                              "selected_tracks": len(evidence.get("tracks", []))},
                    idempotency_key=f"reasoning-question:{request_id}:evidence-prepared", bind=db.get_bind())
    try:
        return await answer_question(evidence, request.question, activity_bind=db.get_bind())
    except (LMStudioError, AIReasoningError) as error:
        record_activity(category="REASONING", event_type="reasoning.question.failed",
                        title="AI question failed", description="The local reasoning request did not return a validated answer.",
                        severity="ERROR", status="FAILED", source="reasoning_api",
                        analysis_job_id=job_id, video_id=job.video_id, error_type=type(error).__name__,
                        metadata={"error_code": error.code},
                        idempotency_key=f"reasoning-question:{request_id}:failed", bind=db.get_bind())
        _raise_reasoning_failure(error)

"""Persistent analysis-job admission, duplicate protection, and listing."""

import threading
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from aai.db.models import AnalysisJob, Video
from aai.schemas.analysis import AnalysisJobCreate

_JOB_ADMISSION_LOCK = threading.Lock()


def create_analysis_job(
    session: Session,
    video_id: str,
    config: AnalysisJobCreate,
    worker,
) -> AnalysisJob:
    with _JOB_ADMISSION_LOCK:
        video = session.get(Video, video_id)
        if video is None:
            raise HTTPException(status_code=404, detail={"code": "video_not_found", "message": "Video not found."})
        if not worker.can_accept():
            raise HTTPException(
                status_code=503,
                detail={"code": "analysis_worker_unavailable", "message": "The analysis worker is not accepting jobs."},
            )
        active_job = session.scalar(
            select(AnalysisJob).where(
                AnalysisJob.video_id == video_id,
                AnalysisJob.status.in_(("queued", "running")),
            )
        )
        if active_job is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_already_active",
                    "message": "This video already has an analysis job in progress.",
                    "job_id": active_job.job_id,
                },
            )

        job = AnalysisJob(
            job_id=str(uuid.uuid4()),
            video_id=video_id,
            status="queued",
            current_stage="Queued",
            confidence_threshold=config.confidence_threshold,
            sampling_rate_fps=config.sampling_rate_fps,
            image_size=config.image_size,
            tracking_enabled=config.tracking_enabled,
        )
        session.add(job)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            active_job = session.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.video_id == video_id,
                    AnalysisJob.status.in_(("queued", "running")),
                )
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_already_active",
                    "message": "This video already has an analysis job in progress.",
                    "job_id": active_job.job_id if active_job else None,
                },
            ) from error
        except SQLAlchemyError as error:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail={"code": "analysis_job_save_failed", "message": "The analysis job could not be saved."},
            ) from error

        try:
            worker.submit(job.job_id)
        except Exception as error:
            session.delete(job)
            session.commit()
            raise HTTPException(
                status_code=503,
                detail={"code": "analysis_queue_unavailable", "message": "The analysis job could not be queued."},
            ) from error
        return job


def get_job(session: Session, job_id: str) -> AnalysisJob:
    try:
        job = session.get(AnalysisJob, job_id)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "analysis_job_lookup_failed", "message": "The analysis job could not be loaded."},
        ) from error
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "analysis_job_not_found", "message": "Analysis job not found."})
    return job


def list_jobs(session: Session, limit: int, offset: int, video_id: str | None = None):
    try:
        statement = select(AnalysisJob)
        count_statement = select(func.count()).select_from(AnalysisJob)
        if video_id is not None:
            statement = statement.where(AnalysisJob.video_id == video_id)
            count_statement = count_statement.where(AnalysisJob.video_id == video_id)
        total = session.scalar(count_statement) or 0
        jobs = session.scalars(
            statement.order_by(AnalysisJob.created_at.desc(), AnalysisJob.job_id).offset(offset).limit(limit)
        ).all()
        return jobs, total
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "analysis_job_list_failed", "message": "Analysis jobs could not be loaded."},
        ) from error


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

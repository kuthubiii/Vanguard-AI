"""Analysis job creation, status, result, and annotated-video endpoints."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aai.db.models import AnalysisJob, Detection
from aai.db.session import get_db
from aai.schemas.analysis import (
    AnalysisJobCreate,
    AnalysisJobListRead,
    AnalysisJobRead,
    AnalysisResultsRead,
    TrackDetailRead,
    TrackSummaryListRead,
    TrackingEventListRead,
    TrackingEventRead,
)
from aai.services.analysis_jobs import create_analysis_job, get_job, list_jobs
from aai.services.analysis_results import (
    annotated_video_path,
    get_detection_page,
    get_track_summaries,
    get_tracking_event_page,
)
from aai.services.analysis_worker import analysis_worker

router = APIRouter(tags=["analysis"])


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _job_read(job: AnalysisJob) -> AnalysisJobRead:
    return AnalysisJobRead(
        job_id=job.job_id,
        video_id=job.video_id,
        status=job.status,
        progress_percentage=job.progress_percentage,
        current_stage=job.current_stage,
        frames_processed=job.frames_processed,
        frames_sampled=job.frames_sampled,
        total_frames=job.total_frames,
        detections_count=job.detections_count,
        error_message=job.error_message,
        confidence_threshold=job.confidence_threshold,
        sampling_rate_fps=job.sampling_rate_fps,
        image_size=job.image_size,
        tracking_enabled=job.tracking_enabled,
        inference_device=job.inference_device,
        created_at=_aware(job.created_at),
        started_at=_aware(job.started_at),
        completed_at=_aware(job.completed_at),
    )


@router.post(
    "/videos/{video_id}/analyses",
    response_model=AnalysisJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_analysis(
    video_id: str,
    response: Response,
    config: AnalysisJobCreate = AnalysisJobCreate(),
    db: Session = Depends(get_db),
) -> AnalysisJobRead:
    job = create_analysis_job(db, video_id, config, analysis_worker)
    response.headers["Location"] = f"/api/analysis-jobs/{job.job_id}"
    return _job_read(job)


@router.get("/analysis-jobs", response_model=AnalysisJobListRead)
def get_analysis_jobs(
    db: Session = Depends(get_db),
    video_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AnalysisJobListRead:
    jobs, total = list_jobs(db, limit, offset, video_id)
    return AnalysisJobListRead(items=[_job_read(job) for job in jobs], total=total, limit=limit, offset=offset)


@router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobRead)
def get_analysis_job(job_id: str, db: Session = Depends(get_db)) -> AnalysisJobRead:
    return _job_read(get_job(db, job_id))


@router.get("/analysis-jobs/{job_id}/results", response_model=AnalysisResultsRead)
def get_analysis_results(
    job_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> AnalysisResultsRead:
    job = get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"code": "analysis_not_complete", "message": "Results are available after analysis completes."},
        )
    try:
        total, detections = get_detection_page(db, job_id, limit, offset)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "analysis_results_failed", "message": "Analysis results could not be loaded."},
        ) from error
    return AnalysisResultsRead(
        job_id=job.job_id,
        video_id=job.video_id,
        total=total,
        limit=limit,
        offset=offset,
        detections=detections,
    )


@router.get("/analysis-jobs/{job_id}/tracking-events", response_model=TrackingEventListRead)
def get_analysis_tracking_events(
    job_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TrackingEventListRead:
    job = get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(status_code=409, detail={"code": "analysis_not_complete", "message": "Tracking events are available after analysis completes."})
    total, events = get_tracking_event_page(db, job_id, limit, offset)
    return TrackingEventListRead(job_id=job_id, total=total, limit=limit, offset=offset, events=events)


@router.get("/analysis-jobs/{job_id}/tracks", response_model=TrackSummaryListRead)
def get_analysis_tracks(
    job_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TrackSummaryListRead:
    job = get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(status_code=409, detail={"code": "analysis_not_complete", "message": "Track summaries are available after analysis completes."})
    summaries, class_counts = get_track_summaries(db, job_id)
    return TrackSummaryListRead(
        job_id=job_id,
        total_unique_tracked_objects=len(summaries),
        total=len(summaries),
        limit=limit,
        offset=offset,
        class_counts=class_counts,
        tracks=summaries[offset:offset + limit],
    )


@router.get("/analysis-jobs/{job_id}/tracks/{track_id}", response_model=TrackDetailRead)
def get_analysis_track(
    job_id: str,
    track_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TrackDetailRead:
    job = get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(status_code=409, detail={"code": "analysis_not_complete", "message": "Track details are available after analysis completes."})
    summaries, _ = get_track_summaries(db, job_id)
    summary = next((item for item in summaries if item.track_id == track_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail={"code": "track_not_found", "message": "No tracked object with that ID exists for this analysis job."})
    # The composite job/track/frame index supports this paginated lookup.
    track_total = db.scalar(select(func.count()).select_from(Detection).where(Detection.job_id == job_id, Detection.track_id == track_id)) or 0
    selected = db.scalars(select(Detection).where(Detection.job_id == job_id, Detection.track_id == track_id).order_by(Detection.frame_index, Detection.detection_id).offset(offset).limit(limit)).all()
    return TrackDetailRead(
        **summary.model_dump(),
        job_id=job_id,
        total_events=track_total,
        limit=limit,
        offset=offset,
        events=[TrackingEventRead.model_validate(item) for item in selected],
    )


@router.get("/analysis-jobs/{job_id}/annotated-video")
@router.head("/analysis-jobs/{job_id}/annotated-video", include_in_schema=False)
def get_annotated_video(job_id: str, db: Session = Depends(get_db)) -> FileResponse:
    job = get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"code": "analysis_not_complete", "message": "The annotated video is available after analysis completes."},
        )
    try:
        path: Path = annotated_video_path(job)
    except (ValueError, OSError) as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "annotated_video_reference_invalid", "message": "The annotated video reference is invalid."},
        ) from error
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "annotated_video_missing", "message": "The annotated video file is not available."},
        )
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"analysis-{job.job_id}.mp4",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )
